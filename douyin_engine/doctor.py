"""Read-only diagnosis, without downloading dependencies or model weights."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
from .engine.sarcasm import SarcasmModel


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--require-model", action="store_true")
    args = parser.parse_args(argv)
    model = SarcasmModel(args.model_dir).status()
    packages = {}
    for package in ("torch", "transformers", "safetensors", "numpy"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    report = {"python": sys.version.split()[0], "core_available": sys.version_info >= (3, 9),
              "model": model, "packages": packages, "model_bundled": False,
              "advice": "规则模式无需安装额外依赖；模型模式需要用户提供兼容且有权使用的本地权重。"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["core_available"] and (not args.require_model or model["status"] in {"available", "loaded"}) else 1
