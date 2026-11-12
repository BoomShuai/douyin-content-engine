"""Actual loopback HTTP acceptance tests; no Internet or model inference.

One temporary LocalServer is served by one background service thread. Every
connection is closed, and server shutdown/join is mandatory at class teardown.
"""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from douyin_engine.server import LocalServer
from douyin_engine.engine.ingest import MAX_BYTES, MAX_ROWS
from douyin_engine.engine.pipeline import AnalysisEngine


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="content-engine-http-test-")
        cls.engine = AnalysisEngine(Path(cls.temporary.name) / "missing-model")
        cls.server = LocalServer(("127.0.0.1", 0), cls.engine)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
            cls.server.server_close()
            cls.thread.join(timeout=5)
            if cls.thread.is_alive():
                raise RuntimeError("HTTP test server thread failed to stop")
        finally:
            cls.temporary.cleanup()

    def request(self, method, path, payload=None, body=None, headers=(), include_host=True, auto_length=True):
        supplied = list(headers)
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        body = b"" if body is None else body
        if isinstance(body, str):
            body = body.encode("utf-8")
        names = [name.lower() for name, _ in supplied]
        if include_host:
            supplied.insert(0, ("Host", "127.0.0.1:%d" % self.port))
        if method == "POST" and "content-type" not in names:
            supplied.append(("Content-Type", "application/json"))
        if method == "POST" and auto_length and "content-length" not in names:
            supplied.append(("Content-Length", str(len(body))))
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for key, value in supplied:
                connection.putheader(key, value)
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def assert_error(self, response, status, code=None):
        actual, headers, body = response
        self.assertEqual(actual, status, body.decode("utf-8", "replace"))
        self.assertIn("application/json", headers["Content-Type"])
        payload = json.loads(body)
        self.assertEqual(set(payload), {"error"})
        self.assertTrue({"code", "message", "details"}.issubset(payload["error"]))
        if code:
            self.assertEqual(payload["error"]["code"], code)
        self.assertNotIn("Traceback", body.decode("utf-8", "replace"))
        self.assertNotIn("/Users/", body.decode("utf-8", "replace"))
        return payload

    def test_health_is_actual_json_and_does_not_load_missing_model(self):
        status, _, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["version"], "1.0.0")
        self.assertEqual(data["limits"], {"max_rows": MAX_ROWS, "max_bytes": MAX_BYTES})
        self.assertEqual(data["model"]["status"], "missing")
        self.assertIsNone(self.engine.model.model)

    def test_real_static_assets_and_security_headers(self):
        for path, mime in (("/", "text/html"), ("/index.html", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css")):
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(mime, headers["Content-Type"])
                self.assertGreater(len(body), 50)
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(headers["X-Frame-Options"], "DENY")
                self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
                self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_real_demo_is_marked_synthetic_and_analyzable(self):
        status, _, body = self.request("GET", "/api/demo")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["source_name"], "synthetic_demo")
        self.assertIn("虚构", data["description"])
        self.assertTrue(data["records"])
        status, _, body = self.request("POST", "/api/analyze", payload={"records": data["records"], "source_name": data["source_name"], "backend": "rules"})
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        self.assertEqual(result["summary"]["total"], len(data["records"]))
        self.assertIn("人工构造", result["warnings"][0])
        self.assertFalse(result["provenance"]["source_verified"])

    def test_real_evidence_endpoint_and_missing_evidence_are_explicit(self):
        status, _, body = self.request("GET", "/api/evidence")
        self.assertEqual(status, 200)
        actual = json.loads(body)
        self.assertIn("available", actual)
        if actual["available"]:
            self.assertEqual(actual["version"], "1.0.0")
            self.assertIn("model_files", actual)
            self.assertIn("sources", actual)
        with tempfile.TemporaryDirectory(prefix="content-engine-evidence-absent-") as directory, patch("douyin_engine.server.ROOT", Path(directory)):
            status, _, body = self.request("GET", "/api/evidence")
        self.assertEqual(status, 200)
        self.assertIs(json.loads(body)["available"], False)

    def test_template_download_is_utf8_bom(self):
        status, headers, body = self.request("GET", "/api/template.csv")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_actual_rules_api_accepts_csv_and_preserves_inert_html_text(self):
        html = '<img src=x onerror="alert(1)">服务好'
        status, _, body = self.request("POST", "/api/analyze", payload={"records": [{"id": "a", "text": html}], "backend": "rules"})
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["comments"][0]["text"], html)
        self.assertIsNone(result["comments"][0]["sarcasm"]["score"])
        status, _, body = self.request("POST", "/api/analyze", payload={"content": "\ufeffid,text\na,服务好", "format": "csv", "backend": "rules"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["summary"]["total"], 1)

    def test_model_missing_api_does_not_return_rule_success(self):
        self.assert_error(self.request("POST", "/api/analyze", payload={"records": [{"text": "服务好"}], "backend": "model"}), 503, "MODEL_MISSING")

    def test_bad_json_nonfinite_json_and_invalid_utf8(self):
        for body in (b"{invalid", b'{"value":NaN}', b"\xff"):
            with self.subTest(body=body):
                self.assert_error(self.request("POST", "/api/analyze", body=body), 400, "INVALID_JSON")

    def test_empty_invalid_and_ambiguous_request_body(self):
        for payload in ([], {"records": []}, {"records": [{"text": "x"}], "content": "[]"}):
            with self.subTest(payload=payload):
                self.assert_error(self.request("POST", "/api/analyze", payload=payload), 400)

    def test_unsupported_content_type_415(self):
        self.assert_error(self.request("POST", "/api/analyze", body="{}", headers=[("Content-Type", "text/plain")]), 415, "CONTENT_TYPE")

    def test_request_and_row_limits_413(self):
        self.assert_error(self.request("POST", "/api/analyze", body=b"{}", headers=[("Content-Length", str(MAX_BYTES * 2 + 16385))]), 413, "TOO_LARGE")
        self.assert_error(self.request("POST", "/api/analyze", payload={"records": [{"text": "fixture"}] * (MAX_ROWS + 1), "backend": "rules"}), 413, "TOO_MANY_ROWS")

    def test_unknown_api_and_sensitive_files_are_not_exposed(self):
        for path in ("/missing", "/api/missing", "/models/sarcasm_m4/model.safetensors", "/engine/ingest.py", "/.env", "/../CONTRACT.md", "/%2e%2e/CONTRACT.md", "/web/../../app.py", "/artifacts/predictions/benchmark_tosarcasm_test_m4.csv"):
            with self.subTest(path=path):
                self.assert_error(self.request("GET", path), 404, "NOT_FOUND")
        self.assert_error(self.request("POST", "/api/missing", payload={}), 404, "NOT_FOUND")

    def test_bad_or_missing_host_rejected(self):
        for headers in ([], [("Host", "attacker.invalid")], [("Host", "127.0.0.1:1")]):
            with self.subTest(headers=headers):
                self.assert_error(self.request("GET", "/api/health", headers=headers, include_host=False), 403, "HOST_DENIED")

    def test_duplicate_host_and_origin_rejected(self):
        self.assert_error(self.request("GET", "/api/health", headers=[("Host", "attacker.invalid")]), 403)
        origins = [("Origin", "http://127.0.0.1:%d" % self.port), ("Origin", "http://attacker.invalid")]
        self.assert_error(self.request("GET", "/api/health", headers=origins), 403)

    def test_same_origin_allowed_but_cross_origin_denied(self):
        status, _, _ = self.request("GET", "/api/health", headers=[("Origin", "http://127.0.0.1:%d" % self.port)])
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", "/api/health", headers=[("Host", "localhost:%d" % self.port)], include_host=False)
        self.assertEqual(status, 200)
        for origin in ("http://attacker.invalid", "null", "https://127.0.0.1:%d" % self.port):
            with self.subTest(origin=origin):
                self.assert_error(self.request("GET", "/api/health", headers=[("Origin", origin)]), 403, "ORIGIN_DENIED")
        self.assert_error(self.request("GET", "/api/health", headers=[("Sec-Fetch-Site", "cross-site")]), 403, "ORIGIN_DENIED")

    def test_preflight_is_denied(self):
        self.assert_error(self.request("OPTIONS", "/api/analyze"), 403, "CORS_DISABLED")

    def test_content_length_and_transfer_encoding_errors(self):
        self.assert_error(self.request("POST", "/api/analyze", body="{}", auto_length=False), 411, "CONTENT_LENGTH")
        self.assert_error(self.request("POST", "/api/analyze", body="{}", headers=[("Content-Length", "2"), ("Content-Length", "2")]), 411, "CONTENT_LENGTH")
        self.assert_error(self.request("POST", "/api/analyze", body="{}", headers=[("Content-Length", "nope")]), 400, "CONTENT_LENGTH")
        self.assert_error(self.request("POST", "/api/analyze", body="{}", headers=[("Transfer-Encoding", "chunked")]), 400, "INVALID_ENCODING")

    def test_busy_server_rejects_without_processing_second_batch(self):
        self.server.analysis_slot.acquire()
        try:
            self.assert_error(self.request("POST", "/api/analyze", payload={"records": [{"text": "x"}], "backend": "rules"}), 503, "BUSY")
        finally:
            self.server.analysis_slot.release()

    def test_error_does_not_leak_private_text_or_exception_path(self):
        response = self.request("POST", "/api/analyze", payload={"records": [{"id": [], "text": "PRIVATE_PAYLOAD_MUST_NOT_LEAK"}], "backend": "rules"})
        self.assert_error(response, 400, "NO_VALID_ROWS")
        self.assertNotIn(b"PRIVATE_PAYLOAD_MUST_NOT_LEAK", response[2])
        with patch.object(self.engine, "analyze", side_effect=RuntimeError("/" + "Users" + "/" + "private" + "/SECRET_KEY")):
            response = self.request("POST", "/api/analyze", payload={})
        self.assert_error(response, 500, "INTERNAL_ERROR")
        self.assertNotIn(b"SECRET_KEY", response[2])
        status, _, _ = self.request("POST", "/api/analyze", payload={"records": [{"text": "fixture"}], "backend": "rules"})
        self.assertEqual(status, 200, "Semaphore must be released even after an unexpected exception")

    def test_non_loopback_bind_rejected_before_opening_socket(self):
        with self.assertRaises(ValueError):
            LocalServer(("0.0.0.0", 0), self.engine)


if __name__ == "__main__":
    unittest.main()
