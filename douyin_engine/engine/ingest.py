"""Bounded CSV/JSON input, explicit missingness, and batch-local pseudonyms."""

import csv
import hashlib
import io
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone, timedelta

from .errors import EngineError

MAX_ROWS = 1000
MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 4000
TZ = timezone(timedelta(hours=8))
FIELDS = {
    "id": ("id", "comment_id", "cid"),
    "text": ("text", "comment", "content", "评论"),
    "context": ("context", "topic", "title", "video_title", "上下文"),
    "author_id": ("author_id", "user_id", "uid"),
    "published_at": ("published_at", "created_at", "create_time", "time", "发布时间"),
    "video_id": ("video_id", "aweme_id"),
    "parent_id": ("parent_id", "parent_comment_id", "reply_to", "reply_id"),
    "likes": ("likes", "digg_count", "like_count", "点赞数"),
    "reply_count": ("reply_count", "reply_comment_total", "回复数"),
}
KNOWN_FIELDS = set(k for names in FIELDS.values() for k in names) | {"source"}
SAFE_DROPPED_NAMES = {"ip", "ip_address", "ip_label", "cookie", "cookies", "phone", "email", "nickname", "name", "avatar", "user", "token", "password", "authorization"}
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
IP = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def decode_json(content):
    # CPython releases have different recursion limits in the C JSON decoder.
    # Enforce a small, deterministic structural limit before invoking it.
    depth, quoted, escaped = 0, False, False
    for character in content:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("JSON nesting exceeds 32 levels")
        elif character in "]}":
            depth -= 1
    def reject_constant(_):
        raise ValueError("non-finite JSON number")
    return json.loads(content, parse_constant=reject_constant)


def parse_content(content, fmt):
    if not isinstance(content, str):
        raise EngineError("INVALID_CONTENT", "文件内容必须是 UTF-8 文本。")
    try:
        content_size = len(content.encode("utf-8"))
    except UnicodeError:
        raise EngineError("INVALID_UNICODE", "文件包含无效Unicode字符。") from None
    if content_size > MAX_BYTES:
        raise EngineError("TOO_LARGE", "文件超过 2 MiB，请分批导入。", 413)
    content = content.lstrip("\ufeff")
    try:
        if fmt == "json":
            rows = decode_json(content)
            if isinstance(rows, dict):
                rows = rows.get("records", rows.get("comments"))
        elif fmt == "csv":
            reader = csv.DictReader(io.StringIO(content), strict=True)
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise EngineError("INVALID_HEADER", "CSV 需要不重复的字段表头。")
            if not any(k in reader.fieldnames for k in FIELDS["text"]):
                raise EngineError("INVALID_HEADER", "CSV 缺少 text/comment/content/评论 字段。")
            if len(reader.fieldnames) > 100:
                raise EngineError("INVALID_HEADER", "CSV 列数不得超过 100。")
            rows = []
            for row in reader:
                if None in row:
                    raise EngineError("CSV_COLUMNS", "CSV 的某行列数多于表头，请检查引号和分隔符。")
                rows.append(row)
                if len(rows) > MAX_ROWS:
                    raise EngineError("TOO_MANY_ROWS", "每批最多 1000 条评论。", 413)
        else:
            raise EngineError("INVALID_FORMAT", "只支持 csv 或 json。")
    except (json.JSONDecodeError, csv.Error, RecursionError, ValueError) as exc:
        if isinstance(exc, EngineError):
            raise
        raise EngineError("PARSE_ERROR", "无法解析文件，请检查 JSON/CSV 格式与引号。") from None
    if not isinstance(rows, list):
        raise EngineError("INVALID_RECORDS", "JSON 应为记录数组，或包含 records 数组的对象。")
    return rows


def _pick(row, field):
    for key in FIELDS[field]:
        if key in row and row[key] is not None and row[key] != "":
            return row[key]
    return None


def _text(value, field, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise EngineError("INVALID_TEXT", field + " 必须是字符串。")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise EngineError("INVALID_UNICODE", field + " 包含无效Unicode字符。") from None
    value = CONTROL.sub("", value).strip()
    if required and not value:
        raise EngineError("EMPTY_TEXT", "评论不能为空。")
    if len(value) > MAX_TEXT:
        raise EngineError("TEXT_TOO_LONG", field + " 超过 4000 字符，请先切分。")
    redacted = 0
    for pattern, replacement in ((PHONE, "[手机号已隐藏]"), (EMAIL, "[邮箱已隐藏]"), (IP, "[IP已隐藏]")):
        value, n = pattern.subn(replacement, value)
        redacted += n
    return value, redacted


def _identifier(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise EngineError("INVALID_ID", "标识字段必须是字符串或整数。")
    value = CONTROL.sub("", str(value)).strip()
    if not value:
        return None
    if len(value) > 160:
        raise EngineError("INVALID_ID", "标识字段超过 160 字符。")
    return value


def parse_timestamp(value):
    """Return (ISO timestamp or None, a missingness/assumption code or None)."""
    if value is None or value == "":
        return None, "MISSING_TIME"
    try:
        if isinstance(value, bool):
            raise ValueError()
        if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d{10}(?:\d{3})?", value)):
            stamp = float(value)
            if not math.isfinite(stamp):
                raise ValueError()
            if stamp >= 1e12:
                stamp /= 1000
            dt = datetime.fromtimestamp(stamp, timezone.utc)
            note = None
        elif isinstance(value, str):
            value = value.strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                datetime.fromisoformat(value)  # Validate the date, but do not invent a clock time.
                return None, "DATE_ONLY_TIME"
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            note = "DATE_ONLY_TIME" if len(value) == 10 else None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
                note = note or "ASSUMED_SHANGHAI_TIME"
        else:
            raise ValueError()
        if dt.year < 1970 or dt.year > 2100:
            raise ValueError()
        return dt.astimezone(TZ).isoformat(), note
    except (ValueError, TypeError, OverflowError, OSError):
        return None, "INVALID_TIME"


def _count(value):
    if value is None or value == "":
        return 0, False
    try:
        if isinstance(value, bool) or isinstance(value, (dict, list)):
            raise ValueError()
        number = float(value)
        if not math.isfinite(number) or number != int(number) or not 0 <= number <= 10**12:
            raise ValueError()
        return int(number), False
    except (ValueError, TypeError, OverflowError):
        return 0, True


def normalize_records(records, source_name="user_import"):
    if not isinstance(records, list) or not records:
        raise EngineError("EMPTY_INPUT", "请导入至少一条评论。")
    if len(records) > MAX_ROWS:
        raise EngineError("TOO_MANY_ROWS", "每批最多 1000 条评论。", 413)
    try:
        raw_size = len(json.dumps(records, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise EngineError("INVALID_RECORDS", "记录包含不支持的值或非有限数字。") from None
    if raw_size > MAX_BYTES:
        raise EngineError("TOO_LARGE", "每批记录最多 2 MiB，请分批导入。", 413)
    if not isinstance(source_name, str) or len(source_name) > 120:
        raise EngineError("INVALID_SOURCE", "来源说明须为不超过 120 字符的字符串。")
    source_name = CONTROL.sub("", source_name).strip() or "user_import"
    source_name = _text(source_name, "来源")[0]
    output, issues, seen, authors, dropped = [], [], {}, {}, set()
    sensitive_ids = {}
    rejected = duplicates = pii_redactions = 0
    assumptions = Counter()
    input_ids = {str(_pick(r, "id")) for r in records if isinstance(r, dict) and _pick(r, "id") is not None}

    def private_id(value):
        nonlocal pii_redactions
        if value is None or not any(pattern.search(value) for pattern in (PHONE, EMAIL, IP)):
            return value
        pii_redactions += 1
        if value not in sensitive_ids:
            replacement = "private-id-%04d" % (len(sensitive_ids) + 1)
            while replacement in input_ids or replacement in sensitive_ids.values():
                replacement += "-"
            sensitive_ids[value] = replacement
        return sensitive_ids[value]
    for pos, raw in enumerate(records, 1):
        try:
            if not isinstance(raw, dict):
                raise EngineError("INVALID_ROW", "记录必须是 JSON 对象。")
            text, n1 = _text(_pick(raw, "text"), "评论", True)
            context, n2 = _text(_pick(raw, "context"), "上下文")
            rid = private_id(_identifier(_pick(raw, "id")))
            if rid is None:
                rid = "auto-" + str(pos)
                while rid in input_ids or rid in seen:
                    rid += "-"
                assumptions["GENERATED_COMMENT_ID"] += 1
            author = _identifier(_pick(raw, "author_id"))
            video = private_id(_identifier(_pick(raw, "video_id")))
            parent = private_id(_identifier(_pick(raw, "parent_id")))
            if parent == "0":
                parent = None
            stamp, note = parse_timestamp(_pick(raw, "published_at"))
            likes, bad_likes = _count(_pick(raw, "likes"))
            replies, bad_replies = _count(_pick(raw, "reply_count"))
            row = {"id": rid, "text": text, "context": context,
                   "author_id": author, "published_at": stamp, "video_id": video,
                   "parent_id": parent, "likes": likes, "reply_count": replies,
                   "source": source_name}
            fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            if rid in seen:
                if fingerprint == seen[rid]:
                    duplicates += 1
                    issues.append({"row": pos, "code": "DUPLICATE_ID", "message": "重复记录已忽略。"})
                    continue
                raise EngineError("CONFLICTING_ID", "重复 ID 对应不同内容，仅保留首次出现的记录。")
            seen[rid] = fingerprint
            if author is not None:
                if author not in authors:
                    authors[author] = "U%04d" % (len(authors) + 1)
                row["author_id"] = authors[author]
            output.append(row)
            pii_redactions += n1 + n2
            dropped.update(k if k in SAFE_DROPPED_NAMES else "[其他字段名已隐藏]" for k in raw if k not in KNOWN_FIELDS)
            if note:
                assumptions[note] += 1
            if bad_likes or bad_replies:
                assumptions["INVALID_COUNT_AS_ZERO"] += 1
                issues.append({"row": pos, "code": "INVALID_COUNT", "message": "互动数无效，按0处理，不用于声称真实热度。"})
            if note == "INVALID_TIME":
                issues.append({"row": pos, "code": note, "message": "时间无法解析；评论保留，但不进入时间线。"})
            if note == "DATE_ONLY_TIME":
                issues.append({"row": pos, "code": note, "message": "仅有日期而无时刻；不补造午夜，评论保留但不进入时间线。"})
        except EngineError as exc:
            rejected += 1
            issues.append({"row": pos, "code": exc.code, "message": exc.message})
    report = {"received": len(records), "accepted": len(output), "rejected": rejected,
              "duplicates": duplicates, "issues": issues,
              "missing": {"time": sum(r["published_at"] is None for r in output),
                          "context": sum(not r["context"] for r in output),
                          "author": sum(r["author_id"] is None for r in output)},
              "assumptions": dict(assumptions), "discarded_fields": sorted(dropped),
              "pii_redactions": pii_redactions,
              "privacy_note": "作者仅保留批次内化名；文本与标识中的常见手机号/邮箱/IP已做规则遮盖，仍可能含个人信息，导出前须人工检查。"}
    if not output:
        raise EngineError("NO_VALID_ROWS", "没有可分析的有效评论。", details=report)
    return output, report


def safe_csv_cell(value):
    text = "" if value is None else str(value)
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r")):
        return "'" + text
    return text


def template_csv():
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(list(FIELDS))
    writer.writerow(["example-1", "这是一条待替换的示例评论", "请填写视频摘要或话题", "local-user-1", "2026-08-31T09:00:00+08:00", "video-1", "", 0, 0])
    return "\ufeff" + output.getvalue()
