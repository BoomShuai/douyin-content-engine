"""Synthetic fixtures only; these tests never request a real platform."""
import importlib.util
import json
import unittest
from unittest.mock import patch
from urllib.parse import quote

from douyin_engine.engine.collector import (
    MAX_BYTES, MAX_COMMENTS, adapt_saved_comments, collect_public_video,
    parse_public_html, validate_video_url,
)


SYNTHETIC_URL = "https://www.douyin.com/video/1234567890"
HAS_INGEST = importlib.util.find_spec("douyin_engine.engine.ingest") is not None


def synthetic_payload():
    """Artificial format example, not a real Douyin response or accuracy data."""
    return {"fixture_kind": "synthetic", "comments": [
        {"cid": "c1", "text": "人工构造：服务很好", "aweme_id": "1234567890",
         "create_time": 1788163200, "digg_count": 3, "reply_comment_total": 1,
         "user": {"uid": "private-user-a", "nickname": "DO_NOT_EXPORT_NICKNAME",
                  "avatar": "DO_NOT_EXPORT_AVATAR"}, "ip_label": "DO_NOT_EXPORT_REGION",
         "reply_comment": [{"cid": "c2", "text": "人工构造：我不同意", "create_time": 1788163260,
                            "user": {"uid": "private-user-b"}}]},
    ]}


class CollectorBoundaryTests(unittest.TestCase):
    def test_explicit_video_url(self):
        self.assertEqual(validate_video_url(SYNTHETIC_URL + "/"), SYNTHETIC_URL)

    def test_reject_urls_outside_exact_contract(self):
        invalid = ["http://www.douyin.com/video/1", "https://v.douyin.com/abc/",
                   "https://www.douyin.com.evil.test/video/1", "https://127.0.0.1/video/1",
                   "https://name" + "@" + "www.douyin.com/video/1", "https://www.douyin.com:443/video/1",
                   SYNTHETIC_URL + "?cookie=secret", SYNTHETIC_URL + "#fragment",
                   "https://www.douyin.com/video/../1", "file:///etc/passwd",
                   "https://www.douyin.com/", SYNTHETIC_URL + "\n"]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_video_url(url)

    def test_reject_nonfinite_timeout(self):
        for timeout in (0, -1, 21, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                collect_public_video(SYNTHETIC_URL, timeout)

    def test_metadata_without_comments_is_not_success(self):
        html = '<html><title>人工构造视频</title><meta name="description" content="人工构造摘要"></html>'
        result = parse_public_html(html, SYNTHETIC_URL)
        self.assertEqual(result["status"], "metadata_only")
        self.assertEqual(result["records"], [])
        self.assertEqual(result["metadata"]["context"], "人工构造摘要")

    def test_captcha_shell_is_blocked(self):
        result = parse_public_html("<title>安全验证</title><p>请完成验证码</p>", SYNTHETIC_URL)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["metadata"]["challenge_hint"])

    def test_login_shell_is_blocked(self):
        result = parse_public_html("<title>视频</title><p>登录后查看评论</p>", SYNTHETIC_URL)
        self.assertEqual(result["status"], "blocked")

    def test_javascript_stays_inert(self):
        html = '<script>window.__data={"comments":[{"text":"not executable"}]};</script>'
        result = parse_public_html(html, SYNTHETIC_URL)
        self.assertEqual(result["status"], "metadata_only")
        self.assertEqual(result["metadata"]["embedded_json_blocks"], 0)

    def test_parser_content_limit(self):
        with self.assertRaises(ValueError):
            parse_public_html("x" * (MAX_BYTES + 1), SYNTHETIC_URL)

    def test_har_and_cookie_exports_rejected(self):
        for payload in ({"log": {"entries": []}}, {"cookies": []}, {"headers": {}}, {"request": {}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                adapt_saved_comments(payload)

    def test_unknown_shape_returns_no_comments(self):
        result = adapt_saved_comments({"data": {"unrelated": "synthetic"}})
        self.assertEqual(result["status"], "no_comments")

    def test_unrelated_text_array_is_not_comments(self):
        html = '<script type="application/json">{"recommendations":[{"text":"人工构造推荐文案"}]}</script>'
        result = parse_public_html(html, SYNTHETIC_URL)
        self.assertEqual(result["status"], "metadata_only")
        self.assertEqual(result["records"], [])

    @patch("douyin_engine.engine.collector._fetch_once")
    def test_external_redirect_stops_before_second_request(self, fetch):
        fetch.return_value = {"state": "redirect", "http_status": 302, "bytes": 0,
                              "location": "https://attacker.invalid/video/123"}
        result = collect_public_video(SYNTHETIC_URL)
        self.assertEqual(result["reason"], "redirect_not_allowlisted")
        self.assertEqual(fetch.call_count, 1)
        self.assertNotIn("attacker", json.dumps(result))

    @patch("douyin_engine.engine.collector.time.sleep")
    @patch("douyin_engine.engine.collector._fetch_once")
    def test_same_host_request_cap(self, fetch, sleep):
        fetch.return_value = {"state": "redirect", "http_status": 302, "bytes": 0,
                              "location": SYNTHETIC_URL}
        result = collect_public_video(SYNTHETIC_URL)
        self.assertEqual(result["reason"], "request_limit")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertGreater(sleep.call_args.args[0], 0)

    @patch("douyin_engine.engine.collector._fetch_once")
    def test_network_error_is_structured_and_not_retried(self, fetch):
        fetch.return_value = {"state": "network_error", "http_status": None, "bytes": 0}
        result = collect_public_video(SYNTHETIC_URL)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["records"], [])

    @patch("douyin_engine.engine.collector._fetch_once")
    def test_non_html_response_stops(self, fetch):
        fetch.return_value = {"state": "ok", "http_status": 200, "bytes": 2,
                              "body": b"{}", "content_type": "application/json"}
        self.assertEqual(collect_public_video(SYNTHETIC_URL)["reason"], "unexpected_content_type")

    @patch("douyin_engine.engine.collector._fetch_once")
    def test_http_success_without_comments_not_collection_success(self, fetch):
        fetch.return_value = {"state": "ok", "http_status": 200, "bytes": 26,
                              "body": b"<title>synthetic</title>", "content_type": "text/html"}
        result = collect_public_video(SYNTHETIC_URL)
        self.assertEqual(result["status"], "metadata_only")
        self.assertNotIn("body", json.dumps(result))


@unittest.skipUnless(HAS_INGEST, "requires engine.ingest integration")
class CollectorAdapterIntegrationTests(unittest.TestCase):
    def test_nested_replies_and_privacy(self):
        result = adapt_saved_comments(synthetic_payload(), context="人工构造语境", video_id="1234567890")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["records"][1]["parent_id"], "c1")
        self.assertIsNotNone(result["records"][0]["published_at"])
        serialized = json.dumps(result)
        for private in ("private-user-a", "private-user-b", "DO_NOT_EXPORT"):
            self.assertNotIn(private, serialized)

    def test_encoded_embedded_json_is_supported(self):
        html = '<script id="RENDER_DATA" type="application/json">' + quote(json.dumps(synthetic_payload())) + '</script>'
        result = parse_public_html(html, SYNTHETIC_URL)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["records"]), 2)

    def test_other_video_is_not_mixed_in(self):
        payload = synthetic_payload()
        payload["comments"].append({"cid": "other", "text": "另一个人工构造视频", "aweme_id": "555"})
        result = adapt_saved_comments(payload, video_id="1234567890")
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["metadata"]["different_video_skipped"], 1)

    def test_duplicate_comment_ids_not_copied_twice(self):
        payload = {"comments": [{"cid": "a", "text": "人工构造一"}, {"cid": "a", "text": "人工构造一"}]}
        result = adapt_saved_comments(payload)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["metadata"]["duplicate_comments_skipped"], 1)

    def test_cap_and_sampling_disclosure(self):
        payload = {"comments": [{"cid": str(i + 1), "text": "人工构造样本"} for i in range(350)]}
        result = adapt_saved_comments(payload)
        self.assertEqual(len(result["records"]), MAX_COMMENTS)
        self.assertEqual(result["metadata"]["observed_comments"], 350)
        self.assertTrue(any("响应出现顺序" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
