"""Bounded, credential-free public-page collection and saved-response adaptation.

No JavaScript is executed. No browser profile, Cookie, proxy, signature or login
is used. A reachable page is not evidence that its comments were retrieved.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib import error, parse, request

MAX_BYTES = 2 * 1024 * 1024
MAX_COMMENTS = 300
MAX_REQUESTS = 2
MIN_REQUEST_INTERVAL = 1.0
ALLOWED_HOST = "www.douyin.com"
_VIDEO_URL = re.compile(r"https://www\.douyin\.com/video/([0-9]{1,30})/?\Z")
_COMMENT_KEYS = {"comments", "comment_list", "commentList"}
_REPLY_KEYS = {"reply_comment", "reply_comments", "replies", "reply_comment_list"}
_BLOCK_MARKERS = ("验证码", "安全验证", "访问验证", "请完成验证", "验证中心", "captcha")


def validate_video_url(url: str) -> str:
    """Only explicit HTTPS public-video URLs; queries and short links are rejected."""
    if not isinstance(url, str) or not _VIDEO_URL.fullmatch(url):
        raise ValueError("仅支持 https://www.douyin.com/video/数字ID；不支持短链、参数、账号或其他主机")
    return url.rstrip("/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch_once(url: str, timeout: float = 10.0) -> dict:
    """One bounded request, no redirects, no proxies or ambient credentials.

    Private helper also permits the public homepage for the boundary-only probe.
    It does not permit arbitrary domains, paths, query strings or credentials.
    """
    if url != "https://www.douyin.com/":
        validate_video_url(url)
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 20:
        raise ValueError("timeout 必须大于0且不超过20秒")
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    req = request.Request(url, headers={
        "User-Agent": "Local-Content-Research/1.0 (public-page-boundary-check; no-cookie)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "identity",
    })
    started = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > MAX_BYTES:
                return {"state": "too_large", "http_status": response.status, "bytes": 0}
            chunks, count = [], 0
            while True:
                if time.monotonic() - started > timeout:
                    return {"state": "timeout", "http_status": response.status, "bytes": count}
                chunk = response.read(min(65536, MAX_BYTES + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
                if count > MAX_BYTES:
                    return {"state": "too_large", "http_status": response.status, "bytes": count}
            return {"state": "ok", "http_status": response.status,
                    "bytes": count, "body": b"".join(chunks),
                    "content_type": response.headers.get("Content-Type", "")}
    except error.HTTPError as exc:
        # Never copy error bodies, Set-Cookie or response headers into output.
        result = {"state": "redirect" if 300 <= exc.code < 400 else "http_error",
                  "http_status": exc.code, "bytes": 0}
        if result["state"] == "redirect":
            result["location"] = exc.headers.get("Location", "")
        exc.close()
        return result
    except (error.URLError, TimeoutError, OSError, HTTPException):
        return {"state": "network_error", "http_status": None, "bytes": 0}


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.visible = []
        self.scripts = []
        self._title = False
        self._script = None
        self._ignore = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self._title = True
        elif tag == "script":
            self._script = []
        elif tag in ("style", "noscript"):
            self._ignore += 1
        elif tag == "meta":
            key = attrs.get("property", attrs.get("name", "")).lower()
            if key in ("og:description", "description") and not self.description:
                self.description = attrs.get("content", "")[:2000]

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        elif tag == "script" and self._script is not None:
            raw = "".join(self._script).strip()
            self._script = None
            # JSON only. JavaScript assignments and executable expressions stay inert.
            for value in (raw, parse.unquote(raw)):
                if value[:1] not in ("{", "["):
                    continue
                try:
                    self.scripts.append(json.loads(value))
                    break
                except (ValueError, RecursionError):
                    pass
        elif tag in ("style", "noscript"):
            self._ignore = max(0, self._ignore - 1)

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        elif self._title:
            self.title += data
        elif not self._ignore and len(self.visible) < 2000:
            self.visible.append(data[:1000])


def _walk_dicts(value: Any, depth: int = 0):
    if depth > 20:
        return
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_dicts(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(item, depth + 1)


def _find_lists(value: Any, depth: int = 0):
    if depth > 20:
        return
    if isinstance(value, list):
        if depth == 0 and value and all(isinstance(item, dict) and ("text" in item or "comment" in item) for item in value):
            yield value
        else:
            for item in value:
                yield from _find_lists(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in _COMMENT_KEYS and isinstance(item, list):
                yield item
            elif key not in _REPLY_KEYS:
                yield from _find_lists(item, depth + 1)


def _nonzero_id(value):
    if value is None or isinstance(value, bool):
        return None
    value = str(value).strip()
    return value if value and value != "0" else None


def adapt_saved_comments(payload: Any, *, context: str = "", video_id: str = "",
                         source_name: str = "saved_public_response") -> dict:
    """Convert a legally saved comments JSON response to normalized contract rows.

    No HAR or request export is accepted. Only explicitly recognized comment
    arrays are read. Authors are immediately pseudonymized by engine.ingest;
    nicknames, avatars, IP labels, tokens, and unused fields are never copied.
    Returns {status, records, metadata, ingest, warnings}.
    """
    if not isinstance(payload, (dict, list)):
        raise ValueError("公开评论响应必须是JSON对象或数组")
    if isinstance(payload, dict) and any(key in payload for key in ("log", "cookies", "request", "headers")):
        raise ValueError("不接受HAR、请求头或Cookie导出；请仅提供已合法保存的评论响应JSON")
    rows, seen_ids = [], set()
    stats = {"observed_comments": 0, "different_video_skipped": 0,
             "duplicate_comments_skipped": 0, "invalid_comments_skipped": 0}

    def emit(items, parent=None, depth=0):
        if depth > 10:
            return
        for item in items:
            if not isinstance(item, dict):
                stats["invalid_comments_skipped"] += 1
                continue
            stats["observed_comments"] += 1
            text = item.get("text", item.get("comment"))
            if not isinstance(text, str) or not text.strip():
                stats["invalid_comments_skipped"] += 1
                continue
            current_video = _nonzero_id(item.get("aweme_id", item.get("video_id")))
            if video_id and current_video and current_video != str(video_id):
                stats["different_video_skipped"] += 1
                continue
            cid = _nonzero_id(item.get("cid", item.get("comment_id", item.get("id"))))
            duplicate = cid and cid in seen_ids
            if duplicate:
                stats["duplicate_comments_skipped"] += 1
            else:
                if cid:
                    seen_ids.add(cid)
                user = item.get("user") if isinstance(item.get("user"), dict) else {}
                parent_id = (_nonzero_id(item.get("reply_to_reply_id")) or
                             _nonzero_id(item.get("reply_id", item.get("parent_id"))) or parent)
                if len(rows) < MAX_COMMENTS:
                    rows.append({
                        "id": cid, "text": text, "context": context,
                        "user_id": user.get("uid", item.get("user_id")),
                        "published_at": item.get("create_time", item.get("published_at")),
                        "video_id": current_video or video_id,
                        "parent_id": parent_id,
                        "likes": item.get("digg_count", item.get("likes", 0)),
                        "reply_count": item.get("reply_comment_total", item.get("reply_count", 0)),
                    })
            for key in _REPLY_KEYS:
                if isinstance(item.get(key), list):
                    emit(item[key], cid, depth + 1)

    for items in _find_lists(payload):
        emit(items)
    warnings = ["仅转换输入响应中实际存在的评论；不是重新抓取，也不代表全量或热榜前300。"]
    valid_count = (stats["observed_comments"] - stats["different_video_skipped"] -
                   stats["invalid_comments_skipped"] - stats["duplicate_comments_skipped"])
    if valid_count > MAX_COMMENTS:
        warnings.append("已达300条上限，按响应出现顺序截取；不是对全平台重要性排序。")
    if not rows:
        return {"status": "no_comments", "records": [], "metadata": stats,
                "ingest": None, "warnings": warnings + ["未找到可识别的评论数组或有效文本。"]}
    from .ingest import normalize_records
    records, report = normalize_records(rows, source_name=source_name)
    return {"status": "ok" if records else "no_comments", "records": records,
            "metadata": {**stats, "retained_comments": len(records)},
            "ingest": report, "warnings": warnings}


def parse_public_html(html: str, source_url: str) -> dict:
    """Parse inert embedded JSON and metadata. Does not fetch or execute scripts."""
    source_url = validate_video_url(source_url)
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_BYTES:
        raise ValueError("页面不是文本或超过2MiB上限")
    video_id = _VIDEO_URL.fullmatch(source_url).group(1)
    page = _PageParser()
    page.feed(html)
    page.close()
    description = page.description
    for obj in _walk_dicts(page.scripts):
        candidate_id = _nonzero_id(obj.get("aweme_id", obj.get("awemeId")))
        if candidate_id == video_id and isinstance(obj.get("desc"), str):
            description = obj["desc"][:2000]
            break
    # Wrapping ensures arbitrary nested "text" arrays are not mistaken for comments.
    parsed = adapt_saved_comments({"embedded_data": page.scripts}, context=description, video_id=video_id,
                                  source_name="public_page_embedded_json")
    visible = (page.title + " " + " ".join(page.visible)).lower()
    challenge = any(marker in visible for marker in _BLOCK_MARKERS)
    login_only = "登录后" in visible and ("评论" in visible or "查看" in visible)
    status = ("ok" if parsed["records"] else
              "blocked" if challenge or login_only else "metadata_only")
    return {**parsed, "status": status, "source_url": source_url,
            "metadata": {**parsed["metadata"], "video_id": video_id,
                         "title": page.title.strip()[:500], "context": description,
                         "embedded_json_blocks": len(page.scripts), "challenge_hint": challenge},
            "warnings": parsed["warnings"] + ([
                "页面没有提供可解析评论；HTTP成功不代表评论采集成功。"
            ] if not parsed["records"] else [])}


def collect_public_video(url: str, timeout: float = 10.0) -> dict:
    """At most two same-host public-video requests, at least one second apart."""
    url = validate_video_url(url)
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 20:
        raise ValueError("timeout 必须大于0且不超过20秒")
    current, started_at = url, _now()
    trace = []
    last_start = None
    for attempt in range(MAX_REQUESTS):
        if last_start is not None:
            time.sleep(max(0.0, MIN_REQUEST_INTERVAL - (time.monotonic() - last_start)))
        last_start = time.monotonic()
        fetched = _fetch_once(current, timeout)
        trace.append({key: fetched[key] for key in ("state", "http_status", "bytes")})
        if fetched["state"] == "redirect":
            location = parse.urljoin(current, fetched.get("location", ""))
            try:
                current = validate_video_url(location)
            except ValueError:
                return {"status": "blocked", "records": [], "source_url": url,
                        "collected_at": started_at, "requests": trace,
                        "reason": "redirect_not_allowlisted",
                        "warnings": ["重定向目的地不在允许的视频URL范围，已停止；未访问目的地。"]}
            continue
        if fetched["state"] != "ok":
            return {"status": "blocked", "records": [], "source_url": url,
                    "collected_at": started_at, "requests": trace,
                    "reason": fetched["state"], "warnings": ["请求受限、失败或超限，已停止；没有评论采集成功证据。"]}
        content_type = fetched.get("content_type", "").lower()
        if "html" not in content_type:
            return {"status": "blocked", "records": [], "source_url": url,
                    "collected_at": started_at, "requests": trace,
                    "reason": "unexpected_content_type", "warnings": ["响应不是HTML页面，已停止。"]}
        html = fetched["body"].decode("utf-8", errors="replace")
        # Decoding invalid UTF-8 may expand bytes; bound before parsing as well.
        if len(html.encode("utf-8")) > MAX_BYTES:
            return {"status": "blocked", "records": [], "source_url": url,
                    "collected_at": started_at, "requests": trace,
                    "reason": "decoded_page_too_large", "warnings": ["页面解码后超过上限。"]}
        parsed = parse_public_html(html, current)
        return {**parsed, "collected_at": started_at, "requests": trace}
    return {"status": "blocked", "records": [], "source_url": url,
            "collected_at": started_at, "requests": trace,
            "reason": "request_limit", "warnings": ["达到2次请求上限，已停止；不继续重试。"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect", help="仅请求指定的公开视频HTML；受限即停止")
    collect.add_argument("url")
    collect.add_argument("--timeout", type=float, default=10)
    collect.add_argument("--output", type=Path)
    saved = sub.add_parser("import-response", help="转换合法保存的评论响应JSON，不接受HAR")
    saved.add_argument("path", type=Path)
    saved.add_argument("--context", default="")
    saved.add_argument("--video-id", default="")
    saved.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.exists():
            raise ValueError("输出文件已存在；请选择新路径")
        if args.command == "collect":
            result = collect_public_video(args.url, args.timeout)
        else:
            with args.path.open("rb") as handle:
                raw = handle.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("响应JSON超过2MiB上限")
            payload = json.loads(raw.decode("utf-8-sig"))
            result = adapt_saved_comments(payload, context=args.context, video_id=args.video_id)
        encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
            print(json.dumps({"status": result["status"], "records": len(result["records"])}, ensure_ascii=False))
        else:
            print(encoded)
        return 0 if result["status"] == "ok" else 2
    except (ValueError, OSError, RecursionError):
        print(json.dumps({"status": "error", "message": "输入或输出无效；请检查路径、JSON格式、大小和视频URL。"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
