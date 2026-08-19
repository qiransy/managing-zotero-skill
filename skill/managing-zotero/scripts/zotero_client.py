from __future__ import annotations

from dataclasses import dataclass, field
import json
import socket
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request


class ZoteroConnectionError(RuntimeError):
    """The local Zotero API could not be reached."""


class ZoteroAuthorizationError(RuntimeError):
    """Zotero declined an authorization or write request."""


class ZoteroVersionConflict(RuntimeError):
    """The target changed since it was read for the current plan."""


class ZoteroLibraryLockedError(RuntimeError):
    """Zotero reports that the library is locked; callers must not retry."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> object:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


@dataclass(frozen=True)
class CapabilityStatus:
    connected: bool
    api_version: str = ""
    schema_version: str = ""
    server_id: str = ""
    write_candidate: bool = False
    reason: str = ""


@dataclass(repr=False)
class LocalAuthorization:
    _key: str = field(repr=False)
    server_id: str
    remember: bool = False
    consumed: bool = False

    def __repr__(self) -> str:
        return "<LocalAuthorization redacted>"

    def consume(self) -> str:
        if self.consumed or not self._key:
            raise ZoteroAuthorizationError("The local authorization has already been consumed")
        key = self._key
        self._key = ""
        self.consumed = True
        return key


class UrllibTransport:
    """Small standard-library transport for Zotero's loopback API."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        if method not in {"GET", "POST"}:
            raise ValueError("Only GET and POST requests are allowed")
        request_headers = dict(headers or {})
        request_headers["Zotero-API-Version"] = "3"
        request_headers["User-Agent"] = "Codex-managing-zotero/1.0"
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as result:
                return HttpResponse(result.status, dict(result.headers.items()), result.read())
        except urllib.error.HTTPError as error:
            return HttpResponse(error.code, dict(error.headers.items()), error.read())
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionRefusedError) as error:
            raise ZoteroConnectionError("Unable to connect to the local Zotero API") from error


class ZoteroClient:
    _WRITE_PREFIXES = ("users/0/collections", "users/0/items")

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:23119/api/",
        transport: Any | None = None,
        timeout: float = 10.0,
    ) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Zotero base URL must use loopback HTTP")
        if parsed.port != 23119:
            raise ValueError("Zotero local API port must be 23119")
        normalized_path = parsed.path.rstrip("/")
        if normalized_path != "/api":
            raise ValueError("Zotero base URL must target /api/")
        self._base_url = base_url.rstrip("/") + "/"
        self._host = parsed.hostname
        self._port = parsed.port
        self._transport = transport or UrllibTransport()
        self._timeout = timeout
        self._capability: CapabilityStatus | None = None
        self._write_disabled_reason = ""

    def probe(self) -> CapabilityStatus:
        try:
            response = self._request("GET", "")
        except ZoteroConnectionError as error:
            status = CapabilityStatus(connected=False, reason=str(error))
            self._capability = status
            return status
        if not 200 <= response.status < 300:
            status = CapabilityStatus(connected=False, reason=f"Local API returned HTTP {response.status}")
            self._capability = status
            return status
        api_version = self._header(response.headers, "Zotero-API-Version")
        schema_version = self._header(response.headers, "Zotero-Schema-Version")
        server_id = self._header(response.headers, "Zotero-Server-ID")
        problems = []
        if api_version != "3":
            problems.append("API version must be 3")
        if not schema_version.isdecimal() or int(schema_version or "0") <= 0:
            problems.append("schema version must be a positive decimal integer")
        if not server_id:
            problems.append("Server ID is missing")
        status = CapabilityStatus(
            connected=True,
            api_version=api_version,
            schema_version=schema_version,
            server_id=server_id,
            write_candidate=not problems,
            reason="; ".join(problems),
        )
        if self._write_disabled_reason:
            status = CapabilityStatus(
                connected=status.connected,
                api_version=status.api_version,
                schema_version=status.schema_version,
                server_id=status.server_id,
                write_candidate=False,
                reason=self._write_disabled_reason,
            )
        self._capability = status
        return status

    def get_json(self, path: str, query: Mapping[str, str] | None = None) -> tuple[object, HttpResponse]:
        response = self._request("GET", path, query=query)
        self._raise_for_response(response, operation="read")
        return response.json(), response

    def authorize_once(self, app_name: str = "Codex managing-zotero") -> LocalAuthorization:
        if self._write_disabled_reason:
            raise ZoteroAuthorizationError(self._write_disabled_reason)
        capability = self.probe()
        if not capability.connected or not capability.write_candidate:
            raise ZoteroAuthorizationError(capability.reason or "Zotero local API is read-only")
        response = self._request(
            "POST",
            "local/authorize",
            headers={"Content-Type": "application/json", "Zotero-Server-ID": capability.server_id},
            body=json.dumps({"appName": app_name}, separators=(",", ":")).encode("utf-8"),
        )
        self._raise_for_response(response, operation="authorization")
        payload = response.json()
        if (
            not isinstance(payload, Mapping)
            or not isinstance(payload.get("key"), str)
            or not payload["key"]
            or payload.get("remember") is not False
        ):
            raise ZoteroAuthorizationError("Zotero did not return a usable one-time authorization")
        server_id = self._header(response.headers, "Zotero-Server-ID") or capability.server_id
        return self._authorization_from_response(payload["key"], False, server_id)

    def post_json(
        self,
        path: str,
        payload: object,
        authorization: LocalAuthorization,
        expected_version: int | None = None,
    ) -> tuple[object, HttpResponse]:
        if not self._is_allowed_write_path(path):
            raise ValueError("This Zotero write endpoint is not allowed")
        if self._write_disabled_reason:
            raise ZoteroAuthorizationError(self._write_disabled_reason)
        capability = self._capability or self.probe()
        if not capability.connected or not capability.write_candidate:
            raise ZoteroAuthorizationError(capability.reason or "Zotero local API is read-only")
        if authorization.server_id != capability.server_id:
            raise ZoteroAuthorizationError("Zotero Server ID changed; obtain a new local authorization")
        key = authorization.consume()
        headers: dict[str, str] = {}
        try:
            headers = {
                "Content-Type": "application/json",
                "Zotero-API-Key": key,
                "Zotero-Server-ID": authorization.server_id,
            }
            if expected_version is not None:
                headers["If-Unmodified-Since-Version"] = str(expected_version)
            response = self._request(
                "POST",
                path,
                headers=headers,
                body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            )
            self._raise_for_response(response, operation="write")
            return response.json(), response
        finally:
            headers.pop("Zotero-API-Key", None)
            key = ""

    def _request(
        self,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        if method not in {"GET", "POST"}:
            raise ValueError("Only GET and POST requests are allowed")
        path_parts = urllib.parse.urlparse(path)
        if path_parts.scheme or path_parts.netloc:
            raise ValueError("Zotero request path must be relative to the local API")
        cleaned_path = path.lstrip("/")
        url = urllib.parse.urljoin(self._base_url, cleaned_path)
        if query:
            url += "?" + urllib.parse.urlencode(query)
        target = urllib.parse.urlparse(url)
        if (
            target.scheme != "http"
            or target.hostname != self._host
            or target.port != self._port
        ):
            raise ValueError("Zotero request must remain on the configured loopback API")
        try:
            response = self._transport.request(method, url, headers=headers, body=body, timeout=self._timeout)
            return self._sanitize_response(response)
        except ZoteroConnectionError:
            raise
        except (OSError, TimeoutError, socket.timeout) as error:
            raise ZoteroConnectionError("Unable to connect to the local Zotero API") from error

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str:
        name_lower = name.lower()
        for key, value in headers.items():
            if key.lower() == name_lower:
                return value
        return ""

    @staticmethod
    def _authorization_from_response(key: str, remember: bool, server_id: str) -> LocalAuthorization:
        return LocalAuthorization(_key=key, server_id=server_id, remember=remember)

    @classmethod
    def _is_allowed_write_path(cls, path: str) -> bool:
        normalized = path.strip("/")
        return any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in cls._WRITE_PREFIXES)

    @staticmethod
    def _sanitize_response(response: HttpResponse) -> HttpResponse:
        headers = {
            name: value
            for name, value in response.headers.items()
            if name.lower() != "zotero-api-key"
        }
        return HttpResponse(response.status, headers, response.body)

    def _latch_read_only(self, reason: str) -> None:
        self._write_disabled_reason = reason
        capability = self._capability
        if capability is None:
            self._capability = CapabilityStatus(connected=True, reason=reason)
            return
        self._capability = CapabilityStatus(
            connected=capability.connected,
            api_version=capability.api_version,
            schema_version=capability.schema_version,
            server_id=capability.server_id,
            write_candidate=False,
            reason=reason,
        )

    def _raise_for_response(self, response: HttpResponse, operation: str) -> None:
        if 200 <= response.status < 300:
            return
        if response.status in {401, 403}:
            raise ZoteroAuthorizationError("Zotero rejected local authorization")
        if response.status in {412, 428}:
            raise ZoteroVersionConflict("Zotero item version conflict")
        if response.status == 409:
            raise ZoteroLibraryLockedError("Zotero library is locked; do not retry automatically")
        if response.status in {405, 501} and operation in {"authorization", "write"}:
            self._latch_read_only("Zotero local API write authorization is unavailable; read-only mode")
            raise ZoteroAuthorizationError("Zotero local API write authorization is unavailable; read-only mode")
        raise ZoteroConnectionError(f"Local Zotero API returned HTTP {response.status}")
