"""Frozen local classifier; no downloads, remote code, or silent rule fallback."""

import importlib.util
import os
import re
import threading
from pathlib import Path

from .errors import EngineError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = Path(os.environ.get("DOUYIN_MODEL_DIR", "models/sarcasm_m4")).expanduser()
MODEL_NAME = "用户配置的本地反讽二分类模型"
REQUIRED_FILES = ("config.json", "model.safetensors", "tokenizer_config.json", "vocab.txt")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")


def clean_model_text(text):
    text = re.sub(r"http\S+|www\.\S+", "", text)
    text = re.sub(r"@\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def encode_input(text, context="", prefix="话题"):
    """Legacy preprocessing retained to match frozen model evaluation."""
    comment = clean_model_text(text)
    topic = clean_model_text(context)
    combined = f"{prefix}：{topic} | 评论：{comment}" if topic else comment
    return combined[:256]


def input_warnings(row):
    comment = clean_model_text(row["text"])
    topic = clean_model_text(row.get("context") or "")
    notes = []
    if not topic:
        notes.append("缺少话题/视频语境，分数只能辅助判断。")
    if not comment:
        notes.append("清洗后没有可分析的评论文字。")
    if len(comment) + (len(topic) + 9 if topic else 0) > 256:
        notes.append("模型按训练协议只读取拼接文本前256字符；请缩短语境或分段复核。")
    if topic and len(topic) + 9 >= 256:
        notes.append("语境过长导致评论未进入模型；此分数不可用于判定。")
    return notes


class SarcasmModel:
    def __init__(self, model_dir=None, device="auto", batch_size=16):
        self.model_dir = Path(model_dir) if model_dir else DEFAULT_MODEL
        if device not in ("auto", "cpu", "mps", "cuda"):
            raise EngineError("INVALID_DEVICE", "设备只能是 auto/cpu/mps/cuda。")
        self.requested_device = device
        self.device = "not_loaded"
        self.batch_size = max(1, min(int(batch_size), 64))
        self.model = self.tokenizer = self.torch = None
        self.lock = threading.RLock()
        self.last_failure = None

    def status(self):
        ready = all((self.model_dir / f).is_file() for f in REQUIRED_FILES)
        dependencies = all(importlib.util.find_spec(p) is not None for p in ("torch", "transformers", "safetensors"))
        status = "loaded" if self.model is not None else ("available" if ready and dependencies else "missing")
        if self.last_failure:
            status = "unavailable"
        return {"status": status, "name": MODEL_NAME, "device": self.device,
                "weights_present": ready, "dependencies_present": dependencies,
                "threshold": 0.5, "review_interval": [0.35, 0.65],
                "score_note": "softmax分数未校准，不等于准确率；跨域与长文本须人工复核。",
                "last_failure": self.last_failure}

    def load(self):
        with self.lock:
            if self.model is not None:
                return
            if not all((self.model_dir / f).is_file() for f in REQUIRED_FILES):
                raise EngineError("MODEL_MISSING", "本地模型文件不完整。请配置兼容的本地二分类模型目录，或明确选择规则模式。", 503)
            try:
                import torch
                from transformers import AutoModelForSequenceClassification, AutoTokenizer
            except ImportError:
                raise EngineError("MODEL_DEPENDENCY", "模型依赖未安装。请安装 model 可选依赖，或明确选择规则模式。", 503) from None
            device = self.requested_device
            if device == "auto":
                device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
            if device == "cuda" and not torch.cuda.is_available() or device == "mps" and not torch.backends.mps.is_available():
                raise EngineError("DEVICE_UNAVAILABLE", "所选设备不可用，请使用 --device cpu。", 503)
            try:
                # Limiting CPU threads avoids oversubscription on small local batches.
                if device == "cpu":
                    torch.set_num_threads(min(4, os.cpu_count() or 1))
                tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir), local_files_only=True, trust_remote_code=False)
                model = AutoModelForSequenceClassification.from_pretrained(str(self.model_dir), local_files_only=True,
                                                                          trust_remote_code=False, use_safetensors=True)
                if model.config.num_labels != 2:
                    raise ValueError("expected binary classifier")
                model.to(device)
                model.eval()
            except Exception:
                self.last_failure = "本地权重加载失败，请检查文件完整性、依赖版本或改用CPU。"
                raise EngineError("MODEL_LOAD_FAILED", self.last_failure, 503) from None
            self.model, self.tokenizer, self.torch, self.device = model, tokenizer, torch, device
            self.last_failure = None

    def predict_scores(self, inputs):
        if not inputs:
            return []
        if not all(isinstance(x, str) for x in inputs):
            raise EngineError("INVALID_MODEL_INPUT", "模型输入必须是字符串列表。")
        with self.lock:
            self.load()
            scores = []
            try:
                with self.torch.inference_mode():
                    for offset in range(0, len(inputs), self.batch_size):
                        batch = self.tokenizer(inputs[offset:offset + self.batch_size], padding="max_length",
                                               truncation=True, max_length=256, return_tensors="pt")
                        batch = {key: value.to(self.device) for key, value in batch.items()}
                        logits = self.model(**batch).logits
                        if not self.torch.isfinite(logits).all():
                            raise ValueError("non-finite model output")
                        scores.extend(self.torch.softmax(logits, dim=-1)[:, 1].cpu().tolist())
            except Exception:
                raise EngineError("MODEL_INFERENCE_FAILED", "模型推理失败。可减少批量、改用CPU；系统没有自动替换为规则预测。", 503) from None
            return scores

    def annotate(self, rows):
        eligible = []
        for index, row in enumerate(rows):
            comment = clean_model_text(row["text"])
            topic = clean_model_text(row.get("context") or "")
            if comment and (not topic or len(topic) + 9 < 256):
                eligible.append(index)
        inputs = [encode_input(rows[i]["text"], rows[i].get("context") or "") for i in eligible]
        scores = dict(zip(eligible, self.predict_scores(inputs)))
        output = []
        for index, row in enumerate(rows):
            notes = input_warnings(row)
            if index not in scores:
                output.append({**row, "sarcasm": {"label": None, "score": None, "available": False,
                                                 "review_required": True, "review_priority": "high", "backend": "model",
                                                 "reasons": notes + ["模型未读取到评论，无法判定；请提供有效文字或缩短上下文。"]}})
                continue
            score = scores[index]
            uncertain = 0.35 <= score <= 0.65
            priority = "high" if uncertain or notes else "normal"
            if uncertain:
                notes.append("分数位于预设复核区间[0.35,0.65]。")
            notes.append("反讽不等于负面情感，需结合原文与语境检查。")
            notes.append("尚未进行抖音域人工校准，所有机器标签都须人工复核。")
            output.append({**row, "sarcasm": {"label": int(score >= 0.5), "score": score,
                                             "available": True, "review_required": True, "review_priority": priority,
                                             "backend": "model", "reasons": notes}})
        return output


def annotate_rules(rows):
    """Explicitly requested heuristics; never invent a model probability."""
    output = []
    for row in rows:
        text = row["text"]
        cues = [word for word in ("呵呵", "真是厉害", "可真行", "不愧是", "狗头", "🙃", "阴阳怪气") if word in text]
        praise = any(word in text for word in ("真棒", "真好", "优秀", "厉害", "贴心"))
        adverse = any(word in text + row.get("context", "") for word in ("出错", "失误", "迟到", "延误", "投诉", "退款", "翻车", "坏了"))
        if praise and adverse:
            cues.append("正面用词与负面情境并存（仅启发式线索）")
        output.append({**row, "sarcasm": {"label": int(bool(cues)), "score": None, "available": True,
                                         "review_required": True, "review_priority": "normal", "backend": "rules",
                                         "reasons": ["规则模式：不使用训练模型，全部结果待复核。"] + cues}})
    return output
