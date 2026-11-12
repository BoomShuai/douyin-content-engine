#!/usr/bin/env python3
"""Batch analysis CLI. Outputs are written only to an explicit, new destination."""

import argparse
import json
import sys
from pathlib import Path

from .engine.errors import EngineError
from .engine.pipeline import AnalysisEngine, export_csv


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 CSV/JSON")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=["model", "rules"], default="rules")
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--bucket", choices=["hour", "day"], default="day")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--source-name", default="user_import")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output already exists. Choose a new path to avoid overwriting a previous analysis.")
    if args.output.suffix.lower() not in (".json", ".csv"):
        parser.error("Output suffix must be .json or .csv")
    try:
        from .engine.ingest import MAX_BYTES
        if args.input.stat().st_size > MAX_BYTES:
            raise EngineError("TOO_LARGE", "输入超过 2 MiB，请分批导入。", 413)
        payload = {"content": args.input.read_text(encoding="utf-8-sig"), "format": args.input.suffix.lower().lstrip("."),
                   "backend": args.backend, "bucket": args.bucket, "source_name": args.source_name}
        result = AnalysisEngine(args.model_dir, args.device).analyze(payload)
        text = export_csv(result) if args.output.suffix.lower() == ".csv" else json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="") as stream:
            stream.write(text)
        print(json.dumps({"output": str(args.output), "run_id": result["run_id"], "summary": result["summary"], "ingest": result["ingest"]}, ensure_ascii=False, indent=2))
    except EngineError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print("无法读写文件，请检查UTF-8编码、路径与权限。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
