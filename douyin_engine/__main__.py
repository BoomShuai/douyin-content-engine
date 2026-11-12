"""Command dispatcher for the standalone local research application."""
import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else None
    if command == "serve":
        from .server import main as run
    elif command == "analyze":
        from .cli import main as run
    elif command == "doctor":
        from .doctor import main as run
    elif command == "collector":
        from .engine.collector import main as run
    else:
        parser = argparse.ArgumentParser(description="知微 · 本地内容分析研究引擎")
        parser.add_argument("command", choices=("serve", "analyze", "doctor", "collector"),
                            help="网页/API、批量分析、安装诊断、可选公开页面/已保存响应适配")
        parser.parse_args(argv)
        return 2
    return run(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
