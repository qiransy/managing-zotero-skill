"""Deterministic loopback-only Zotero Local API simulation for tests."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class RequestRecord:
    method: str
    path: str
    headers: Mapping[str, str]
    body: Any
    object_versions: Mapping[str, int]
    failure_mode: str


class FakeZoteroServer:
    """Stateful fake that never contacts Zotero or stores real literature."""

    def __init__(self, *, test_mode: bool = False, failure_mode: str = "") -> None:
        if test_mode is not True:
            raise ValueError("FakeZoteroServer requires explicit test_mode=True")
        self.failure_mode = failure_mode
        self.library_version = 1
        self.server_id = "FAKE-SERVER-1"
        self.authorization_count = 0
        self.write_count = 0
        self.delete_count = 0
        self.records: list[RequestRecord] = []
        self.collections: dict[str, dict[str, Any]] = {}
        self.items: dict[str, dict[str, Any]] = {}
        self.children: dict[str, list[dict[str, Any]]] = {}
        self._issued_keys: list[str] = []
        self._valid_key = ""
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def api_url(self) -> str:
        if self._server is None:
            raise RuntimeError("fake server is not running")
        return f"http://127.0.0.1:{self._server.server_port}/api/"

    def __enter__(self) -> FakeZoteroServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner._handle(self)

            def do_POST(self) -> None:
                owner._handle(self)

            def do_DELETE(self) -> None:
                owner.delete_count += 1
                owner._record(self, None)
                owner._send(self, 405, {"error": "method not allowed"})

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
        self._valid_key = ""

    def contains_issued_key(self, text: str) -> bool:
        return any(key and key in text for key in self._issued_keys)

    def set_failure(self, mode: str) -> None:
        self.failure_mode = mode

    def rotate_server_id(self) -> None:
        suffix = int(self.server_id.rsplit("-", 1)[-1]) + 1
        self.server_id = f"FAKE-SERVER-{suffix}"

    def seed_collection(self, key: str, name: str, version: int = 1) -> None:
        self.collections[key] = {"key": key, "name": name, "version": version, "parentCollection": False}
        self.library_version = max(self.library_version, version)

    def seed_item(
        self,
        key: str,
        *,
        title: str,
        doi: str,
        collections: tuple[str, ...] = (),
        version: int = 1,
        personal_note: str = "",
    ) -> None:
        self.items[key] = {
            "key": key,
            "version": version,
            "itemType": "journalArticle",
            "title": title,
            "DOI": doi,
            "collections": list(collections),
            "tags": [{"tag": "personal"}],
        }
        if personal_note:
            self.children[key] = [{
                "key": f"NOTE-{key}",
                "version": version,
                "itemType": "note",
                "parentItem": key,
                "note": personal_note,
            }]
        self.library_version = max(self.library_version, version)

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        body = self._read_body(handler)
        self._record(handler, body)
        path = parsed.path.rstrip("/") or "/"

        if self.failure_mode == "local_api_disabled":
            self._send(handler, 403, {"error": "local API disabled"})
            return
        if path == "/api" and handler.command == "GET":
            self._send(handler, 200, {}, root=True)
            return
        if path == "/api/local/authorize" and handler.command == "POST":
            self._authorize(handler)
            return

        prefix = "/api/users/0/"
        if not path.startswith(prefix):
            self._send(handler, 404, {"error": "not found"})
            return
        resource_path = path[len(prefix):]
        parts = resource_path.split("/")
        resource = parts[0]
        key = parts[1] if len(parts) > 1 else ""
        child_request = len(parts) == 3 and parts[2] == "children"
        if resource not in {"collections", "items"}:
            self._send(handler, 404, {"error": "not found"})
            return

        if handler.command == "GET":
            if resource == "items" and key and child_request:
                self._send(handler, 200, self.children.get(key, []), library_header=True)
            elif key:
                store = self.collections if resource == "collections" else self.items
                value = store.get(key)
                self._send(handler, 200 if value else 404, self._wrap(value) if value else {"error": "not found"}, library_header=True)
            else:
                values = self._filter(resource, parse_qs(parsed.query))
                self._send(handler, 200, [self._wrap(value) for value in values], library_header=True)
            return

        if handler.command == "POST" and not key:
            self._write(handler, resource, body)
            return
        self._send(handler, 405, {"error": "method not allowed"})

    def _authorize(self, handler: BaseHTTPRequestHandler) -> None:
        if self.failure_mode in {"authorize_405", "authorize_501"}:
            self._send(handler, int(self.failure_mode[-3:]), {"error": "write unsupported"})
            return
        if self.failure_mode == "authorization_denied":
            self._send(handler, 401, {"error": "authorization denied"})
            return
        if handler.headers.get("Zotero-Server-ID") != self.server_id:
            self._send(handler, 401, {"error": "server identity mismatch"})
            return
        self.authorization_count += 1
        key = secrets.token_urlsafe(24)
        self._issued_keys.append(key)
        self._valid_key = key
        if self.failure_mode == "server_id_changed_on_authorize":
            self.rotate_server_id()
        self._send(handler, 200, {"key": key, "remember": False}, root=True)

    def _write(self, handler: BaseHTTPRequestHandler, resource: str, body: Any) -> None:
        if handler.headers.get("Zotero-Server-ID") != self.server_id:
            self._send(handler, 401, {"error": "server identity mismatch"})
            return
        supplied_key = handler.headers.get("Zotero-API-Key", "")
        if not self._valid_key or not secrets.compare_digest(supplied_key, self._valid_key):
            self._send(handler, 401, {"error": "invalid one-time authorization"})
            return
        self._valid_key = ""
        self.write_count += 1
        if self.failure_mode == "library_locked":
            self._send(handler, 409, {"error": "library locked"})
            return
        if self.failure_mode == "stale_write":
            self._send(handler, 412, {"error": "stale preview"})
            return
        expected = handler.headers.get("If-Unmodified-Since-Version")
        if expected is not None and expected != str(self.library_version):
            self._send(handler, 412, {"error": "library version conflict"})
            return
        payloads = list(body) if isinstance(body, list) else []
        successful: dict[str, Any] = {}
        unchanged: dict[str, Any] = {}
        failed: dict[str, Any] = {}
        for index, payload in enumerate(payloads):
            slot = str(index)
            if not isinstance(payload, Mapping):
                failed[slot] = {"code": 400, "message": "invalid object"}
                continue
            if self.failure_mode == "mixed_items" and index == len(payloads) - 1:
                failed[slot] = {"code": 400, "message": "injected item failure"}
                continue
            store = self.collections if resource == "collections" else self.items
            key = str(payload.get("key", ""))
            existing = store.get(key) if key else None
            if existing is not None and payload.get("version") != existing.get("version"):
                failed[slot] = {"code": 412, "message": "object version conflict"}
                continue
            if not key:
                key = ("COL" if resource == "collections" else "ITEM") + f"{len(store) + 1:05d}"
            candidate = dict(existing or {})
            candidate.update(dict(payload))
            candidate["key"] = key
            if existing is not None and all(existing.get(name) == value for name, value in payload.items() if name != "version"):
                unchanged[slot] = {"key": key, "version": existing["version"]}
                continue
            self.library_version += 1
            candidate["version"] = self.library_version
            store[key] = candidate
            successful[slot] = {"key": key, "version": candidate["version"]}
        self._send(handler, 200, {"successful": successful, "unchanged": unchanged, "failed": failed}, library_header=True)

    def _filter(self, resource: str, query: Mapping[str, list[str]]) -> list[dict[str, Any]]:
        store = self.collections if resource == "collections" else self.items
        values = list(store.values())
        text = (query.get("q") or [""])[0].casefold()
        doi = (query.get("doi") or [""])[0].casefold()
        collection = (query.get("collectionKey") or [""])[0]
        if text:
            field = "name" if resource == "collections" else "title"
            values = [value for value in values if text in str(value.get(field, "")).casefold()]
        if resource == "items" and doi:
            values = [value for value in values if str(value.get("DOI", "")).casefold() == doi]
        if resource == "items" and collection:
            values = [value for value in values if collection in value.get("collections", ())]
        return values

    def _record(self, handler: BaseHTTPRequestHandler, body: Any) -> None:
        headers = {
            name: "[REDACTED]" if name.casefold() == "zotero-api-key" else value
            for name, value in handler.headers.items()
        }
        versions = {
            **{key: int(value["version"]) for key, value in self.collections.items()},
            **{key: int(value["version"]) for key, value in self.items.items()},
        }
        self.records.append(RequestRecord(handler.command, handler.path, headers, body, versions, self.failure_mode))

    @staticmethod
    def _read_body(handler: BaseHTTPRequestHandler) -> Any:
        try:
            size = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        raw = handler.rfile.read(size) if size else b""
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "[INVALID BODY]"

    @staticmethod
    def _wrap(value: Mapping[str, Any] | None) -> dict[str, Any]:
        if value is None:
            return {}
        return {"key": value["key"], "version": value["version"], "data": dict(value)}

    def _send(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: Any,
        *,
        root: bool = False,
        library_header: bool = False,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        if root:
            handler.send_header("Zotero-API-Version", "3")
            handler.send_header("Zotero-Schema-Version", "44")
            handler.send_header("Zotero-Server-ID", self.server_id)
        if library_header:
            handler.send_header("Last-Modified-Version", str(self.library_version))
        handler.end_headers()
        handler.wfile.write(body)
