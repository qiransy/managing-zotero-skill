import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotero_client import (
    HttpResponse,
    ZoteroAuthorizationError,
    ZoteroClient,
    ZoteroConnectionError,
    ZoteroLibraryLockedError,
    ZoteroVersionConflict,
    UrllibTransport,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    @classmethod
    def root_ok(cls):
        return cls([
            HttpResponse(
                200,
                {"Zotero-API-Version": "3", "Zotero-Schema-Version": "44", "Zotero-Server-ID": "SERVER-ONE"},
                b"{}",
            )
        ])

    @classmethod
    def authorization_ok(cls, key, remember):
        return cls([
            HttpResponse(
                200,
                {"Zotero-API-Version": "3", "Zotero-Schema-Version": "44", "Zotero-Server-ID": "SERVER-ONE"},
                b"{}",
            ),
            HttpResponse(200, {"Zotero-Server-ID": "SERVER-ONE"}, json.dumps({"key": key, "remember": remember}).encode()),
            HttpResponse(200, {"Last-Modified-Version": "8"}, b"[]"),
        ])

    def request(self, method, url, headers=None, body=None, timeout=10.0):
        self.requests.append((method, url, dict(headers or {}), body))
        return self.responses.pop(0)


class ClientTests(unittest.TestCase):
    def test_rejects_non_loopback_base_url(self):
        with self.assertRaises(ValueError):
            ZoteroClient("https://api.zotero.org/")

    def test_probe_reads_versions_and_server_id(self):
        client = ZoteroClient(transport=FakeTransport.root_ok())
        status = client.probe()
        self.assertTrue(status.connected)
        self.assertTrue(status.write_candidate)
        self.assertEqual(status.api_version, "3")
        self.assertEqual(status.schema_version, "44")
        self.assertEqual(status.server_id, "SERVER-ONE")

    def test_authorization_key_is_redacted_and_consumed(self):
        transport = FakeTransport.authorization_ok(key="top-secret", remember=False)
        client = ZoteroClient(transport=transport)
        authorization = client.authorize_once()
        self.assertNotIn("top-secret", repr(authorization))
        _, response = client.post_json("users/0/collections", [{"name": "test"}], authorization)
        self.assertTrue(authorization.consumed)
        self.assertNotIn("top-secret", repr(response.headers))
        with self.assertRaises(ZoteroAuthorizationError):
            client.post_json("users/0/collections", [{"name": "again"}], authorization)

    def test_delete_method_is_rejected(self):
        client = ZoteroClient(transport=FakeTransport.root_ok())
        with self.assertRaises(ValueError):
            client._request("DELETE", "users/0/items/ABCD2345")

    def test_authorization_failure_maps_to_authorization_error(self):
        transport = FakeTransport([
            HttpResponse(
                200,
                {"Zotero-API-Version": "3", "Zotero-Schema-Version": "44", "Zotero-Server-ID": "SERVER-ONE"},
                b"{}",
            ),
            HttpResponse(401, {}, b"denied"),
        ])
        client = ZoteroClient(transport=transport)
        with self.assertRaises(ZoteroAuthorizationError):
            client.authorize_once()

    def test_write_conflict_maps_to_version_conflict_and_consumes_key(self):
        transport = FakeTransport([
            HttpResponse(
                200,
                {"Zotero-API-Version": "3", "Zotero-Schema-Version": "44", "Zotero-Server-ID": "SERVER-ONE"},
                b"{}",
            ),
            HttpResponse(412, {}, b"stale"),
        ])
        client = ZoteroClient(transport=transport)
        authorization = client._authorization_from_response("secret", False, "SERVER-ONE")
        with self.assertRaises(ZoteroVersionConflict):
            client.post_json("users/0/collections", [], authorization, expected_version=7)
        self.assertTrue(authorization.consumed)

    def test_connection_error_is_mapped_without_request_headers(self):
        class RefusingTransport:
            def request(self, method, url, headers=None, body=None, timeout=10.0):
                raise OSError("connection refused")

        client = ZoteroClient(transport=RefusingTransport())
        with self.assertRaises(ZoteroConnectionError) as raised:
            client.get_json("users/0/collections")
        self.assertNotIn("Zotero-API-Key", str(raised.exception))

    def test_server_id_change_blocks_write_before_sending_secret(self):
        transport = FakeTransport([
            HttpResponse(
                200,
                {"Zotero-API-Version": "3", "Zotero-Schema-Version": "44", "Zotero-Server-ID": "SERVER-ONE"},
                b"{}",
            ),
        ])
        client = ZoteroClient(transport=transport)
        client.probe()
        authorization = client._authorization_from_response("secret", False, "SERVER-TWO")
        with self.assertRaises(ZoteroAuthorizationError):
            client.post_json("users/0/collections", [], authorization)
        self.assertFalse(authorization.consumed)
        self.assertEqual(len(transport.requests), 1)

    def test_locked_library_is_not_retried(self):
        transport = FakeTransport([
            HttpResponse(
                200,
                {"Zotero-API-Version": "3", "Zotero-Schema-Version": "44", "Zotero-Server-ID": "SERVER-ONE"},
                b"{}",
            ),
            HttpResponse(409, {}, b"locked"),
        ])
        client = ZoteroClient(transport=transport)
        authorization = client._authorization_from_response("secret", False, "SERVER-ONE")
        with self.assertRaises(ZoteroLibraryLockedError):
            client.post_json("users/0/collections", [], authorization)
        self.assertEqual(len(transport.requests), 2)

    def test_transport_forces_api_version_and_non_browser_user_agent(self):
        class Reply:
            status = 200
            headers = {}

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        with patch("zotero_client.urllib.request.urlopen", return_value=Reply()) as open_url:
            UrllibTransport().request(
                "GET", "http://127.0.0.1:23119/api/", headers={"Zotero-API-Version": "9"}
            )
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Zotero-api-version"), "3")
        self.assertNotIn("Mozilla", request.get_header("User-agent"))

    def test_read_path_cannot_replace_loopback_origin(self):
        transport = FakeTransport([])
        client = ZoteroClient(transport=transport)
        with self.assertRaises(ValueError):
            client.get_json("https://api.zotero.org/users/0/items")
        self.assertEqual(transport.requests, [])

    def test_echoed_authorization_key_is_removed_from_response_headers(self):
        transport = FakeTransport([
            HttpResponse(
                200,
                {"Zotero-API-Version": "3", "Zotero-Schema-Version": "44", "Zotero-Server-ID": "SERVER-ONE"},
                b"{}",
            ),
            HttpResponse(200, {"Zotero-API-Key": "top-secret"}, b"[]"),
        ])
        client = ZoteroClient(transport=transport)
        authorization = client._authorization_from_response("top-secret", False, "SERVER-ONE")
        _, response = client.post_json("users/0/collections", [], authorization)
        self.assertNotIn("zotero-api-key", {name.lower() for name in response.headers})
        self.assertNotIn("top-secret", repr(response.headers))

    def test_incompatible_or_malformed_capability_is_read_only(self):
        for api_version, schema_version in (("2", "44"), ("3", "0"), ("3", "forty-four")):
            with self.subTest(api_version=api_version, schema_version=schema_version):
                transport = FakeTransport([
                    HttpResponse(
                        200,
                        {
                            "Zotero-API-Version": api_version,
                            "Zotero-Schema-Version": schema_version,
                            "Zotero-Server-ID": "SERVER-ONE",
                        },
                        b"{}",
                    ),
                ])
                status = ZoteroClient(transport=transport).probe()
                self.assertTrue(status.connected)
                self.assertFalse(status.write_candidate)

    def test_unavailable_authorization_latches_read_only_capability(self):
        transport = FakeTransport([
            HttpResponse(
                200,
                {"Zotero-API-Version": "3", "Zotero-Schema-Version": "44", "Zotero-Server-ID": "SERVER-ONE"},
                b"{}",
            ),
            HttpResponse(405, {}, b"not supported"),
        ])
        client = ZoteroClient(transport=transport)
        with self.assertRaises(ZoteroAuthorizationError):
            client.authorize_once()
        self.assertFalse(client._capability.write_candidate)

    def test_unavailable_write_latches_read_only_capability(self):
        transport = FakeTransport([
            HttpResponse(
                200,
                {"Zotero-API-Version": "3", "Zotero-Schema-Version": "44", "Zotero-Server-ID": "SERVER-ONE"},
                b"{}",
            ),
            HttpResponse(501, {}, b"not supported"),
        ])
        client = ZoteroClient(transport=transport)
        authorization = client._authorization_from_response("secret", False, "SERVER-ONE")
        with self.assertRaises(ZoteroAuthorizationError):
            client.post_json("users/0/collections", [], authorization)
        self.assertFalse(client._capability.write_candidate)

    def test_authorization_rejects_persistent_or_malformed_remember_value(self):
        for remember in (True, "false"):
            with self.subTest(remember=remember):
                transport = FakeTransport([
                    HttpResponse(
                        200,
                        {"Zotero-API-Version": "3", "Zotero-Schema-Version": "44", "Zotero-Server-ID": "SERVER-ONE"},
                        b"{}",
                    ),
                    HttpResponse(200, {"Zotero-Server-ID": "SERVER-ONE"}, json.dumps({"key": "secret", "remember": remember}).encode()),
                ])
                with self.assertRaises(ZoteroAuthorizationError):
                    ZoteroClient(transport=transport).authorize_once()


if __name__ == "__main__":
    unittest.main()
