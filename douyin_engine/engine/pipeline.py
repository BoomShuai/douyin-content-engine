"""Shared application pipeline for HTTP, CLI and regression checks."""

import csv
import hashlib
import io
import json
import time
from datetime import datetime, timezone

from . import __version__
from .errors import EngineError
from .ingest import normalize_records, parse_content, safe_csv_cell
from .sarcasm import SarcasmModel, annotate_rules


class AnalysisEngine:
    def __init__(self, model_dir=None, device="auto"):
        self.model = SarcasmModel(model_dir, device)

    def analyze(self, payload):
        from .analytics import analyze_records
        started = time.perf_counter()
        if not isinstance(payload, dict):
            raise EngineError("INVALID_REQUEST", "请求必须是 JSON 对象。")
        backend = payload.get("backend", "rules")
        bucket = payload.get("bucket", "day")
        if backend not in ("model", "rules"):
            raise EngineError("INVALID_BACKEND", "请选择 model 或 rules，系统不会自动降级。")
        if bucket not in ("day", "hour"):
            raise EngineError("INVALID_BUCKET", "时间粒度只能是 day 或 hour。")
        if "records" in payload and "content" in payload:
            raise EngineError("AMBIGUOUS_INPUT", "records 和 content 只能提供一个。")
        raw = payload.get("records") if "records" in payload else parse_content(payload.get("content"), payload.get("format"))
        rows, ingest = normalize_records(raw, payload.get("source_name", "user_import"))
        canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        predicted = self.model.annotate(rows) if backend == "model" else annotate_rules(rows)
        result = analyze_records(predicted, bucket=bucket)
        warnings = result.setdefault("warnings", [])
        warnings.append("统计仅代表当前导入样本；按热度抽样、时间缺失或采集不全会带来偏差，不能外推全网比例。")
        if backend == "rules":
            warnings.append("当前是显式规则模式：未运行RoBERTa模型，反讽标签不提供概率且全部待复核。")
        if payload.get("source_name") == "synthetic_demo":
            warnings.insert(0, "此批数据为人工构造的虚构演示，不是真实抖音采集，也不能用于评估模型准确率。")
        if ingest["rejected"] or ingest["duplicates"]:
            warnings.append("部分输入未进入分析，请查看数据质量记录与逐行原因。")
        warnings.append(ingest["privacy_note"])
        now = datetime.now(timezone.utc).isoformat()
        result.update({"version": __version__, "run_id": input_hash[:12] + "-" + str(time.time_ns()),
                       "created_at": now, "backend": backend,
                       "model": self.model.status() if backend == "model" else {"name": "规则启发式 v1.0", "status": "rules", "device": "cpu"},
                       "ingest": ingest, "elapsed_seconds": round(time.perf_counter() - started, 4),
                       "provenance": {"input_sha256": input_hash, "source_name": rows[0]["source"],
                                      "source_verified": False, "storage": "只在进程内存中分析；不自动保存用户数据。",
                                      "bucket": bucket, "timezone": "Asia/Shanghai",
                                      "review_note": "机器输出不是人工标注；人工复核须单独导出，并保留原始预测。"}})
        return result


def export_csv(result):
    fields = ["id", "text", "context", "author_id", "published_at", "video_id", "parent_id", "likes",
              "sarcasm_prediction", "sarcasm_score", "review_required", "sentiment", "opinions_json", "backend"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in result["comments"]:
        item = {k: row.get(k) for k in fields if k in row}
        item.update({"sarcasm_prediction": row["sarcasm"]["label"], "sarcasm_score": row["sarcasm"]["score"],
                     "review_required": row["sarcasm"]["review_required"], "sentiment": row["sentiment"]["label"],
                     "opinions_json": json.dumps(row["opinions"], ensure_ascii=False), "backend": result["backend"]})
        writer.writerow({k: safe_csv_cell(v) for k, v in item.items()})
    return "\ufeff" + stream.getvalue()
