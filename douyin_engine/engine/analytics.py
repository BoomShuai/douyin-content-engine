"""Deterministic, evidence-bounded analytics for normalized comments.

This is an interpretable lexical baseline, not a learned opinion extractor.
No model score is used to reverse sentiment; no missing author, time or edge is
inferred. All algorithms use only the Python 3.9+ standard library.
"""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    SHANGHAI = ZoneInfo("Asia/Shanghai")
    _TIMEZONE_NOTE = "Asia/Shanghai"
except ZoneInfoNotFoundError:
    # Modern platform timestamps use UTC+08:00. This fallback is declared, not
    # silently presented as historical daylight-saving-time support.
    SHANGHAI = timezone(timedelta(hours=8))
    _TIMEZONE_NOTE = "UTC+08:00（系统缺少Asia/Shanghai时区库；历史夏令时未处理）"


MAX_DENSE_BUCKETS = 2000
MIN_TURNING_POINT_SAMPLES = 3
TURNING_POINT_MIN_DELTA = 0.35

# Longest-first matching prevents e.g. “不好” being counted again as “好”.
_POSITIVE_WORDS = (
    "好用", "好看", "好吃", "好评", "不错", "不赖", "不差", "不贵",
    "没毛病", "没有问题", "喜欢", "满意", "支持", "推荐", "优秀",
    "出色", "精彩", "漂亮", "方便", "实用", "可靠", "真诚", "认真",
    "负责", "贴心", "耐心", "热情", "专业", "良心", "合理", "值得",
    "便宜", "清晰", "安全", "舒适", "有效", "公平", "成功", "进步",
    "改善", "惊喜", "赞同", "赞成", "省心", "流畅", "稳定", "靠谱",
    "划算", "幸福", "开心", "快乐", "感谢", "赞", "棒", "好", "快",
)
_NEGATIVE_WORDS = (
    "不好用", "不好看", "不好吃", "不值得", "不喜欢", "不满意",
    "不支持", "不推荐", "不合理", "不公平", "不靠谱", "不安全",
    "不可靠", "不稳定", "不专业", "不好", "不行", "好不到哪",
    "讨厌", "失望", "差评", "糟糕", "垃圾", "恶心", "骗人", "欺骗",
    "坑爹", "劣质", "投诉", "不满", "反对", "抵制", "离谱", "敷衍",
    "虚假", "危险", "崩溃", "无语", "失败", "退步", "难用", "卡顿",
    "尴尬", "难看", "低劣", "滥用", "暴力", "压榨", "欺负", "生气",
    "失责", "污染", "造假", "欺诈", "漏洞", "差", "坏", "慢", "贵",
    "烂", "骗", "坑",
)
_LEXICON = {term: 1 for term in _POSITIVE_WORDS}
_LEXICON.update({term: -1 for term in _NEGATIVE_WORDS})
_LEXICON_BY_FIRST = defaultdict(list)
for _term in sorted(_LEXICON, key=lambda term: (-len(term), term)):
    _LEXICON_BY_FIRST[_term[0]].append(_term)

# These strings contain single-character polarity tokens without asserting
# that polarity. The list is deliberately visible and versioned in source.
_PROTECTED_TERMS = (
    "快递", "快照", "快捷键", "快门", "快进", "快手", "好奇", "好像",
    "好比", "好歹", "好几", "好多", "好些", "爱好", "好不容易",
    "慢性", "慢慢", "贵阳", "贵州", "贵姓", "贵公司", "贵族",
    "差不多", "差异", "差距", "差旅", "差点", "差额", "时差",
    "误差", "差别", "差事", "差价", "价差", "好评返现",
)
_PROTECTED_PATTERN = re.compile("|".join(map(re.escape, _PROTECTED_TERMS)))
_NEGATORS = re.compile("没有|不是|不能|不会|从不|从未|并未|并不|未曾|不曾|没|不|未|无|别|莫")
_NEGATOR_EXCLUSIONS = re.compile(
    "不但|不仅|不光|不止|不得不|不管|不论|无论|无疑|无比|无条件|"
    "没想到|没料到|别看|莫非|不能更"
)
_INTENSIFIERS = re.compile("非常|特别|十分|极其|超级|有点|稍微|很|太|挺|真")
_CLAUSE_BREAK = re.compile(
    r"[，,。！？!?；;\n\r]+|但是|然而|可是|不过(?!分)|(?<!不)但(?!凡|愿|是|当)|却|而且|并且"
)
_CONTRAST_TERMS = {"但是", "然而", "可是", "不过", "但", "却"}
_ATTRIBUTION = re.compile("他说|她说|他们说|有人说|网友说|听说|据说|客服说|老板说|朋友说|引用|转述")

_TARGET_TERMS = (
    "性价比", "售后服务", "服务", "客服", "态度", "物流", "快递", "配送",
    "包装", "质量", "产品", "商品", "手机", "耳机", "电脑", "屏幕",
    "电池", "续航", "价格", "味道", "食物", "餐厅", "食品", "店铺",
    "商家", "店家", "主播", "视频", "内容", "演技", "剧情", "电影",
    "音乐", "教学", "课程", "老师", "医生", "医院", "政策", "方案",
    "规则", "平台", "软件", "系统", "界面", "功能", "算法", "操作",
    "广告", "流量", "售后", "退款", "发货", "交通", "环境", "卫生",
    "设施", "体验", "设计", "工作", "工资", "老板", "公司", "员工",
)
_TARGET_PATTERN = re.compile("|".join(map(re.escape, sorted(_TARGET_TERMS, key=len, reverse=True))))
_TOPIC_DICTIONARY = (
    ("服务与售后", ("服务", "客服", "售后", "退款", "投诉", "态度")),
    ("物流与配送", ("物流", "快递", "配送", "发货", "包裹", "签收")),
    ("产品与性能", ("质量", "产品", "商品", "手机", "耳机", "电脑", "电池", "续航", "性能", "卡顿")),
    ("价格与消费", ("价格", "便宜", "昂贵", "太贵", "优惠", "消费", "性价比", "划算", "购买")),
    ("内容与创作", ("视频", "主播", "内容", "创作", "直播", "博主", "广告", "带货")),
    ("食品与餐饮", ("食物", "食品", "餐厅", "味道", "好吃", "难吃", "外卖", "卫生")),
    ("教育与学习", ("教育", "学习", "课程", "老师", "教学", "考试", "学生", "学校")),
    ("公共事务", ("政策", "公共", "治理", "交通", "环境", "污染", "规则")),
    ("隐私与安全", ("隐私", "安全", "泄露", "诈骗", "欺诈", "漏洞", "个人信息")),
    ("劳动与就业", ("工作", "就业", "员工", "加班", "工资", "劳动", "招聘", "裁员")),
    ("影视与娱乐", ("电影", "演技", "剧情", "音乐", "演员", "歌手", "电视剧", "综艺")),
    ("平台与技术", ("平台", "算法", "系统", "软件", "界面", "功能", "流量", "推荐机制")),
)


def _clauses(text):
    """Return original-string spans; contrast changes lexical weighting only."""
    start, weight = 0, 1.0
    result = []
    for separator in _CLAUSE_BREAK.finditer(text):
        result.append((start, separator.start(), weight))
        weight = 1.5 if separator.group() in _CONTRAST_TERMS else 1.0
        start = separator.end()
    result.append((start, len(text), weight))
    return [(left, right, weight) for left, right, weight in result if text[left:right].strip()]


def _overlaps(start, end, spans):
    return any(start < right and end > left for left, right in spans)


def _lexical_matches(text, clause_start, clause_end, clause_weight):
    clause = text[clause_start:clause_end]
    # A per-character mask avoids quadratic overlap checks on long repeated
    # compounds (e.g. many copies of “贵州”), including adversarial imports.
    protected = bytearray(len(clause))
    for match in _PROTECTED_PATTERN.finditer(clause):
        protected[match.start():match.end()] = b"\x01" * (match.end() - match.start())
    matches, position, previous_end = [], 0, 0
    while position < len(clause):
        term = next(
            (word for word in _LEXICON_BY_FIRST.get(clause[position], ())
             if clause.startswith(word, position)
             and not any(protected[position:position + len(word)])),
            None,
        )
        if term is None:
            position += 1
            continue

        # A local 10-character window never crosses a previous sentiment token
        # or a clause boundary. This scope is heuristic, not a syntax parser.
        prefix_start = max(previous_end, position - 10)
        prefix = clause[prefix_start:position]
        exclusions = [(match.start(), match.end()) for match in _NEGATOR_EXCLUSIONS.finditer(prefix)]
        negators = [match for match in _NEGATORS.finditer(prefix)
                    if not _overlaps(match.start(), match.end(), exclusions)]
        polarity = _LEXICON[term] * (-1 if len(negators) % 2 else 1)
        evidence_start = position
        if negators:
            evidence_start = prefix_start + negators[0].start()
        intensity = 1.0
        intensifiers = list(_INTENSIFIERS.finditer(prefix))
        if intensifiers and len(prefix) - intensifiers[-1].end() <= 3:
            modifier = intensifiers[-1]
            intensity = 0.7 if modifier.group() in {"有点", "稍微"} else 1.3
            evidence_start = min(evidence_start, prefix_start + modifier.start())
        end = position + len(term)
        matches.append({
            "term": term,
            "start": clause_start + evidence_start,
            "end": clause_start + end,
            "term_start": clause_start + position,
            "polarity": polarity,
            "negation_count": len(negators),
            "weight": clause_weight * intensity,
            "evidence": text[clause_start + evidence_start:clause_start + end],
        })
        position = previous_end = end
    return matches


def _sentiment_and_opinions(text, author_id):
    all_matches, opinions = [], []
    for left, right, clause_weight in _clauses(text):
        matches = _lexical_matches(text, left, right, clause_weight)
        all_matches.extend(matches)
        raw_clause = text[left:right]
        trimmed_left = left + len(raw_clause) - len(raw_clause.lstrip())
        trimmed_right = right - (len(raw_clause) - len(raw_clause.rstrip()))
        evidence = text[trimmed_left:trimmed_right]
        targets = list(_TARGET_PATTERN.finditer(raw_clause))
        holder = None if _ATTRIBUTION.search(raw_clause) else author_id
        for match in matches:
            relative_position = match["term_start"] - left
            preceding = [target for target in targets if target.end() <= relative_position]
            following = [target for target in targets if target.start() >= relative_position]
            # Explicit mention only: no pronoun/coreference resolution or
            # implicit recovery of a target from the video's context.
            target = preceding[-1] if preceding else (following[0] if following else None)
            opinions.append({
                "holder": holder,
                "target": target.group() if target else None,
                "opinion": match["evidence"],
                "sentiment": "positive" if match["polarity"] > 0 else "negative",
                "evidence": evidence,
                "start": trimmed_left,
                "end": trimmed_right,
                "method": "lexicon_span_v1",
                "review_required": True,
            })

    weighted_sum = math.fsum(match["polarity"] * match["weight"] for match in all_matches)
    total_weight = math.fsum(match["weight"] for match in all_matches)
    score = round(weighted_sum / (total_weight + 1.0), 6) if all_matches else 0.0
    label = "positive" if score > 0.1 else ("negative" if score < -0.1 else "neutral")
    sentiment = {
        "label": label,
        "score": score,
        "evidence": [match["evidence"] for match in all_matches],
        "method": "lexicon_literal_v1",
        "matches": all_matches,
    }
    return sentiment, opinions


def _topic_terms(record):
    # Context informs topic only; it never becomes evidence for an opinion in
    # the comment, nor a source of the comment's literal sentiment.
    text = record.get("text", "") + "\n" + record.get("context", "")
    return [topic for topic, aliases in _TOPIC_DICTIONARY if any(alias in text for alias in aliases)]


def _parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(SHANGHAI)
    except (ValueError, OverflowError):
        return None


def _bucket_time(timestamp, bucket):
    timestamp = timestamp.replace(minute=0, second=0, microsecond=0)
    return timestamp.replace(hour=0) if bucket == "day" else timestamp


def _sarcasm(record):
    value = record.get("sarcasm")
    return value if isinstance(value, Mapping) else {}


def _is_review(record):
    return _sarcasm(record).get("review_required") is True


def _is_sarcastic(record):
    return _sarcasm(record).get("label") == 1


def _is_sarcasm_scorable(record):
    value = _sarcasm(record)
    label = value.get("label")
    return (isinstance(label, int) and not isinstance(label, bool)
            and label in (0, 1) and value.get("available") is not False)


def _representative_ids(records):
    if not records:
        return []
    choices = [min(records, key=lambda row: row["sentiment"]["score"]),
               max(records, key=lambda row: row["sentiment"]["score"])]
    choices.extend(row for row in records if _is_review(row))
    choices.extend(records)
    result = []
    for record in choices:
        record_id = record.get("id")
        if record_id and record_id not in result:
            result.append(record_id)
        if len(result) == 3:
            break
    return result


def _make_bucket(timestamp, records):
    scores = [record["sentiment"]["score"] for record in records]
    count = len(scores)
    mean = math.fsum(scores) / count if count else 0.0
    variance = math.fsum((score - mean) ** 2 for score in scores) / count if count else 0.0
    topics = Counter(topic for record in records for topic in record["_topics"])
    return {
        "time": timestamp.isoformat(),
        "count": count,
        "sentiment_mean": round(mean, 8),
        "sentiment_variance": round(variance, 8),
        "sarcasm_count": sum(_is_sarcastic(record) for record in records),
        "sarcasm_scored_count": sum(_is_sarcasm_scorable(record) for record in records),
        "unscorable_count": sum(not _is_sarcasm_scorable(record) for record in records),
        "review_count": sum(_is_review(record) for record in records),
        "positive_count": sum(record["sentiment"]["label"] == "positive" for record in records),
        "negative_count": sum(record["sentiment"]["label"] == "negative" for record in records),
        "neutral_count": sum(record["sentiment"]["label"] == "neutral" for record in records),
        "representative_ids": _representative_ids(records),
        "topics": [term for term, _ in sorted(topics.items(), key=lambda item: (-item[1], item[0]))[:3]],
        "is_empty": count == 0,
    }


def _timeline(comments, bucket):
    groups, invalid_count = defaultdict(list), 0
    for comment in comments:
        timestamp = _parse_time(comment.get("published_at"))
        if timestamp is None:
            invalid_count += 1
            continue
        groups[_bucket_time(timestamp, bucket)].append(comment)
    warnings, result = [], []
    if invalid_count:
        warnings.append("%d条评论缺少有效带时区时间，已排除时间线；未补造发布时间。" % invalid_count)
    if not groups:
        return [], [], warnings, 0
    observed = sorted(groups)
    step = timedelta(days=1) if bucket == "day" else timedelta(hours=1)
    span = int((observed[-1] - observed[0]) / step) + 1
    if span <= MAX_DENSE_BUCKETS:
        # Indexed addition avoids advancing past datetime.max after the last
        # valid bucket, an important boundary for adversarial input.
        times = [observed[0] + index * step for index in range(span)]
    else:
        times = observed
        warnings.append(
            "时间跨度为%d个%s桶，超过%d桶上限；仅展示有样本桶，gap_before明确记录缺失间隔。"
            % (span, "日" if bucket == "day" else "小时", MAX_DENSE_BUCKETS)
        )
    previous_time = None
    for timestamp in times:
        item = _make_bucket(timestamp, groups.get(timestamp, []))
        if previous_time is not None and timestamp - previous_time > step:
            item["gap_before"] = {
                "empty_buckets": int((timestamp - previous_time) / step) - 1,
                "from": (previous_time + step).isoformat(),
                "to": (timestamp - step).isoformat(),
            }
        result.append(item)
        previous_time = timestamp

    turning_points = []
    for previous, current in zip(result, result[1:]):
        if current.get("gap_before") or min(previous["count"], current["count"]) < MIN_TURNING_POINT_SAMPLES:
            continue
        delta = current["sentiment_mean"] - previous["sentiment_mean"]
        if abs(delta) < TURNING_POINT_MIN_DELTA:
            continue
        turning_points.append({
            "time": current["time"],
            "delta": round(delta, 8),
            "direction": "more_positive" if delta > 0 else "more_negative",
            "evidence_ids": list(dict.fromkeys(previous["representative_ids"] + current["representative_ids"])),
            "description": (
                "相邻时间桶的导入样本字面情感均值从%.3f变为%.3f（n=%d→%d）。"
                "这是词典样本变化提示，未做显著性检验，不能据此认定真实舆情反转、事件或因果。"
                % (previous["sentiment_mean"], current["sentiment_mean"], previous["count"], current["count"])
            ),
        })
    return result, turning_points, warnings, sum(len(rows) for rows in groups.values())


def _nonnegative_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _author_id(record):
    author = record.get("author_id")
    return author.strip() if isinstance(author, str) and author.strip() else None


def _pagerank(author_ids, edge_weights, damping=0.85, tolerance=1e-12, max_iterations=200):
    """Weighted directed PageRank, including uniform dangling redistribution."""
    size = len(author_ids)
    if not size:
        return {}, 0, True
    ranks = {author: 1.0 / size for author in author_ids}
    outgoing_weight = Counter()
    for (source, _), weight in edge_weights.items():
        outgoing_weight[source] += weight
    converged = False
    for iteration in range(1, max_iterations + 1):
        dangling_mass = math.fsum(ranks[author] for author in author_ids if not outgoing_weight[author])
        base = (1.0 - damping) / size + damping * dangling_mass / size
        new_ranks = {author: base for author in author_ids}
        for (source, target), weight in edge_weights.items():
            new_ranks[target] += damping * ranks[source] * weight / outgoing_weight[source]
        distance = math.fsum(abs(new_ranks[author] - ranks[author]) for author in author_ids)
        ranks = new_ranks
        if distance < tolerance:
            converged = True
            break
    total = math.fsum(ranks.values())
    return {author: value / total for author, value in ranks.items()}, iteration, converged


def _network(comments):
    author_records = defaultdict(list)
    ids = Counter(comment.get("id") for comment in comments if comment.get("id"))
    by_id = {comment["id"]: comment for comment in comments
             if comment.get("id") and ids[comment["id"]] == 1}
    unknown_author_count = 0
    for comment in comments:
        author = _author_id(comment)
        if author is None:
            unknown_author_count += 1
        else:
            author_records[author].append(comment)
    edge_weights, missing_parent_count, self_reply_count = Counter(), 0, 0
    unavailable_author_edge_count = 0
    for comment in comments:
        parent_id = comment.get("parent_id")
        if not parent_id:
            continue
        if parent_id not in by_id:
            missing_parent_count += 1
            continue
        source, target = _author_id(comment), _author_id(by_id[parent_id])
        if source is None or target is None:
            unavailable_author_edge_count += 1
        elif source == target:
            self_reply_count += 1
        else:
            edge_weights[(source, target)] += 1
    author_ids = sorted(author_records)
    ranks, iterations, converged = _pagerank(author_ids, edge_weights)
    in_neighbors, out_neighbors = defaultdict(set), defaultdict(set)
    in_replies, out_replies = Counter(), Counter()
    for (source, target), weight in edge_weights.items():
        in_neighbors[target].add(source)
        out_neighbors[source].add(target)
        in_replies[target] += weight
        out_replies[source] += weight
    likes = {author: sum(_nonnegative_int(row.get("likes")) for row in rows)
             for author, rows in author_records.items()}
    max_rank = max(ranks.values(), default=1.0)
    max_likes = max(likes.values(), default=0)
    max_comments = max((len(rows) for rows in author_records.values()), default=0)
    nodes = []
    for author in author_ids:
        records = author_records[author]
        # math.log(int) handles very large Python integers without first
        # casting them to an overflowing float (unlike math.log1p).
        like_score = math.log(likes[author] + 1) / math.log(max_likes + 1) if max_likes else 0.0
        comment_score = math.log(len(records) + 1) / math.log(max_comments + 1) if max_comments else 0.0
        if edge_weights:
            score = 100 * (0.4 * ranks[author] / max_rank + 0.35 * like_score + 0.25 * comment_score)
        else:
            # With no reply edges PageRank is uniformly uninformative. Do not
            # award it an influence contribution in the composite score.
            score = 100 * (0.6 * like_score + 0.4 * comment_score)
        ranked_records = sorted(records, key=lambda row: (-_nonnegative_int(row.get("likes")), str(row.get("id", ""))))
        nodes.append({
            "id": author,
            "comment_count": len(records),
            "likes": likes[author],
            "in_degree": len(in_neighbors[author]),
            "out_degree": len(out_neighbors[author]),
            "in_reply_count": in_replies[author],
            "out_reply_count": out_replies[author],
            "pagerank": ranks[author],
            "score": round(score, 6),
            "evidence_ids": [row["id"] for row in ranked_records[:5] if row.get("id")],
        })
    nodes.sort(key=lambda node: (-node["score"], -node["pagerank"], node["id"]))
    edges = [{"source": source, "target": target, "weight": weight}
             for (source, target), weight in sorted(edge_weights.items())]
    warnings = ["节点排名仅描述导入批次的相对互动可见度，不是幕后推手、身份或操纵行为的证据。"]
    if not edge_weights:
        warnings.append("没有可验证的跨作者回复边；PageRank为均匀分配，综合分仅使用已导入点赞与发言数。")
    if missing_parent_count:
        warnings.append("%d条回复的父评论不在批次内或ID有歧义，未建立边。" % missing_parent_count)
    if unknown_author_count:
        warnings.append("%d条评论没有已知作者，未凭空创建作者节点。" % unknown_author_count)
    if unavailable_author_edge_count:
        warnings.append("%d条回复至少一端缺少作者，未建立边。" % unavailable_author_edge_count)
    if self_reply_count:
        warnings.append("%d条同作者回复已排除，避免自回边抬高互动排名。" % self_reply_count)
    if not converged:
        warnings.append("PageRank在200次迭代内未达到1e-12收敛阈值；当前返回归一化近似值。")
    return {
        "nodes": nodes,
        "edges": edges,
        "missing_parent_count": missing_parent_count,
        "unknown_author_count": unknown_author_count,
        "self_reply_excluded_count": self_reply_count,
        "unavailable_author_edge_count": unavailable_author_edge_count,
        "pagerank_iterations": iterations,
        "pagerank_converged": converged,
        "method": "observed_reply_graph_weighted_pagerank_v1",
        "warnings": warnings,
    }


def analyze_records(records, bucket="day"):
    """Analyze normalized records without mutating them.

    Invalid/naive dates are omitted from time-series analysis, not interpreted
    using the host timezone. Input schema normalization belongs to ingest.py.
    """
    if bucket not in {"day", "hour"}:
        raise ValueError("bucket必须为day或hour")
    if not isinstance(records, (list, tuple)) or any(not isinstance(row, Mapping) for row in records):
        raise ValueError("records必须为规范记录列表")
    comments = []
    for record in records:
        if not isinstance(record.get("text", ""), str) or not isinstance(record.get("context", ""), str):
            raise ValueError("规范记录的text和context必须为字符串")
        comment = dict(record)
        comment["sentiment"], comment["opinions"] = _sentiment_and_opinions(comment.get("text", ""), _author_id(comment))
        comment["_topics"] = _topic_terms(comment)
        comments.append(comment)

    timeline, turning_points, time_warnings, dated_count = _timeline(comments, bucket)
    topic_records = defaultdict(list)
    for comment in comments:
        for topic in comment["_topics"]:
            topic_records[topic].append(comment.get("id", ""))
    topics = [{"term": term, "count": len(ids), "comment_ids": ids}
              for term, ids in sorted(topic_records.items(), key=lambda item: (-len(item[1]), item[0]))]
    network = _network(comments)
    summary = {
        "total": len(comments),
        "sarcasm_count": sum(_is_sarcastic(comment) for comment in comments),
        "sarcasm_scored_count": sum(_is_sarcasm_scorable(comment) for comment in comments),
        "unscorable_count": sum(not _is_sarcasm_scorable(comment) for comment in comments),
        "review_count": sum(_is_review(comment) for comment in comments),
        "positive_count": sum(comment["sentiment"]["label"] == "positive" for comment in comments),
        "negative_count": sum(comment["sentiment"]["label"] == "negative" for comment in comments),
        "neutral_count": sum(comment["sentiment"]["label"] == "neutral" for comment in comments),
        "opinion_count": sum(len(comment["opinions"]) for comment in comments),
        "dated_count": dated_count,
        "author_count": len(network["nodes"]),
        "topic_covered_count": sum(bool(comment["_topics"]) for comment in comments),
    }
    for comment in comments:
        del comment["_topics"]
    warnings = [
        "情感结果是中文词典字面倾向，不等于真实态度或心理状态；反讽分数不会自动翻转情感。",
        "观点四元组是需人工复核的词典跨度基线；未知评价对象留空，隐式指代与复杂引语未解决。",
    ] + time_warnings
    if not comments:
        warnings.append("没有可分析记录；返回空结果，未生成虚构指标。")
    if _TIMEZONE_NOTE != "Asia/Shanghai":
        warnings.append(_TIMEZONE_NOTE)
    return {
        "summary": summary,
        "comments": comments,
        "timeline": timeline,
        "turning_points": turning_points,
        "topics": topics,
        "network": network,
        "warnings": warnings,
        "methodology": {
            "sentiment": (
                "lexicon_literal_v1：最长词典匹配；分句内前10字符否定窗口，不跨上一情感词；"
                "转折后分句权重1.5，程度词权重1.3/0.7；分数=有符号权重和/(总权重+1)，"
                "超过±0.1为正/负，其余为中性。分数不是概率；未覆盖词汇/复杂语义可能误判。"
            ),
            "opinions": (
                "lexicon_span_v1：评价人取已知作者（检测到转述时留空），目标取同分句最近显式词典对象，"
                "观点为情感证据词组，证据为原文分句[start,end)字符跨度。全部四元组需人工复核。"
            ),
            "topics": "固定中文主题词典，评论或上下文命中计一次；可多标签，不是BERTopic、聚类或LLM抽取。",
            "timeline": (
                "时间桶统一%s；仅有效带时区记录入桶；方差为桶内总体方差；count=0时均值/方差0仅为空桶占位，"
                "不代表观察到中性。≤%d桶完整保留空桶，长跨度通过gap_before显式标注间隔。"
                % (_TIMEZONE_NOTE, MAX_DENSE_BUCKETS)
            ),
            "turning_points": (
                "仅比较相邻非空时间桶，每桶至少%d条且字面情感均值差绝对值≥%.2f；"
                "不跨缺口、不做显著性推断、不识别真实事件或因果。"
                % (MIN_TURNING_POINT_SAMPLES, TURNING_POINT_MIN_DELTA)
            ),
            "network": (
                "仅已知作者且父评论在批次内的真实回复构成有向边：回复者→被回复者；排除同作者边。"
                "边权为回复条数，in_degree/out_degree为不同相邻作者数，in_reply_count/out_reply_count为回复条数。"
                "PageRank阻尼0.85，悬空质量均匀分配。综合可见度0–100：有边时40%归一化PageRank、"
                "35%对数点赞、25%对数发言数；无边时60%对数点赞、40%对数发言数。"
                "归一化以批次最大值为分母，不能跨批次比较；每节点最多列5条证据。"
            ),
            "review_count": "summary/timeline的review_count仅统计反讽分支review_required；四元组具有独立复核标志。",
            "unscorable_count": (
                "sarcasm_scored_count统计有0/1标签且available不为false的记录（含显式规则标签，不代表概率）；"
                "其余计入unscorable_count。无法判定不是非反讽，不能将total-sarcasm_count解释为非反讽数。"
            ),
            "scope": "本地导入数据的可解释研究基线；没有替代真实平台采集验收、人工标注评测或因果分析。",
        },
    }
