#!/usr/bin/env python3
"""Loopback-only, zero-third-party-dependency HTTP shell for the research engine."""

import argparse
import json
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .engine import __version__
from .engine.errors import EngineError
from .engine.ingest import MAX_BYTES, MAX_ROWS, template_csv, decode_json
from .engine.pipeline import AnalysisEngine

ROOT = Path(__file__).resolve().parent
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, engine):
        if address[0] != "127.0.0.1":
            raise ValueError("This research server must bind to 127.0.0.1 only")
        super().__init__(address, Handler)
        self.engine = engine
        self.analysis_slot = threading.BoundedSemaphore(1)


class Handler(BaseHTTPRequestHandler):
    server_version = "ResearchEngine/1.0"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, fmt, *args):
        # No body, query string, user text, identifiers, or filesystem paths in logs.
        return

    def _guards(self):
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if len(self.headers.get_all("Host") or []) != 1 or self.headers.get("Host") not in allowed:
            raise EngineError("HOST_DENIED", "仅允许本机同源访问。", 403)
        if len(self.headers.get_all("Origin") or []) > 1:
            raise EngineError("ORIGIN_DENIED", "Origin 请求头有歧义。", 403)
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in allowed}:
            raise EngineError("ORIGIN_DENIED", "拒绝跨站请求。", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise EngineError("ORIGIN_DENIED", "拒绝跨站请求。", 403)

    def _send(self, status, body, mime="application/json; charset=utf-8", download=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Connection", "close")
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="' + download + '"')
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, exc):
        self._send(exc.status, exc.as_dict())

    def do_GET(self):
        try:
            self._guards()
            path = urlsplit(self.path).path
            if path in STATIC:
                name, mime = STATIC[path]
                file = ROOT / "web" / name
                if not file.is_file():
                    raise EngineError("ASSET_MISSING", "界面文件不完整，请检查交付包。", 503)
                self._send(200, file.read_bytes(), mime)
            elif path == "/api/health":
                self._send(200, {"ok": True, "version": __version__, "model": self.server.engine.model.status(),
                                 "limits": {"max_rows": MAX_ROWS, "max_bytes": MAX_BYTES}})
            elif path == "/api/demo":
                records = json.loads((ROOT / "data" / "demo.json").read_text(encoding="utf-8"))
                self._send(200, {"records": records, "source_name": "synthetic_demo",
                                 "description": "虚构的配送服务讨论：仅用于操作与功能演示，并非抖音真实抓取。"})
            elif path == "/api/template.csv":
                self._send(200, template_csv().encode("utf-8"), "text/csv; charset=utf-8", "comments_template.csv")
            elif path == "/api/evidence":
                self._send(200, {"available": False, "version": __version__,
                                 "message": "公开版不分发历史评测、逐条预测或训练数据；没有可供展示的准确率声明。",
                                 "model_bundled": False,
                                 "validation": "见项目 docs/VALIDATION.md；软件测试不等于模型效果评估。"})
            else:
                raise EngineError("NOT_FOUND", "资源不存在。", 404)
        except EngineError as exc:
            self._error(exc)
        except (OSError, ValueError, KeyError):
            self._error(EngineError("LOCAL_FILE_ERROR", "本地资源不可用，请校验交付包。", 500))

    def do_POST(self):
        acquired = False
        try:
            self._guards()
            if urlsplit(self.path).path != "/api/analyze":
                raise EngineError("NOT_FOUND", "接口不存在。", 404)
            if self.headers.get("Transfer-Encoding"):
                raise EngineError("INVALID_ENCODING", "不支持分块请求。", 400)
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                raise EngineError("CONTENT_TYPE", "请求类型须为 application/json。", 415)
            lengths = self.headers.get_all("Content-Length") or []
            if len(lengths) != 1:
                raise EngineError("CONTENT_LENGTH", "需要唯一的 Content-Length。", 411)
            try:
                length = int(lengths[0])
            except ValueError:
                raise EngineError("CONTENT_LENGTH", "Content-Length 无效。") from None
            if length <= 0:
                raise EngineError("EMPTY_INPUT", "请求内容不能为空。")
            # JSON string escaping may roughly double the uploaded file size.
            if length > MAX_BYTES * 2 + 16384:
                raise EngineError("TOO_LARGE", "请求超过允许大小，请分批导入。", 413)
            acquired = self.server.analysis_slot.acquire(blocking=False)
            if not acquired:
                raise EngineError("BUSY", "当前有一批分析正在进行，请完成后再试。", 503)
            body = self.rfile.read(length)
            if len(body) != length:
                raise EngineError("INCOMPLETE_REQUEST", "请求未完整传输。")
            try:
                payload = decode_json(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                raise EngineError("INVALID_JSON", "JSON 格式无效。") from None
            result = self.server.engine.analyze(payload)
            self._send(200, result)
        except EngineError as exc:
            self._error(exc)
        except (socket.timeout, TimeoutError):
            self._error(EngineError("REQUEST_TIMEOUT", "请求传输超时。", 408))
        except Exception:
            self._error(EngineError("INTERNAL_ERROR", "分析失败；请运行交付包自检或缩小批量重试。未自动保存输入。", 500))
        finally:
            if acquired:
                self.server.analysis_slot.release()

    def do_OPTIONS(self):
        self._error(EngineError("CORS_DISABLED", "此本地引擎不支持跨站调用。", 403))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "mps", "cuda"])
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--open", action="store_true", help="Open the local interface in the default browser")
    parser.add_argument("--preload", action="store_true", help="Load the local model before serving")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be 1024..65535")
    engine = AnalysisEngine(args.model_dir, args.device)
    if args.preload:
        try:
            engine.model.load()
        except EngineError as exc:
            print(exc.message, file=sys.stderr)
            return 1
    try:
        server = LocalServer(("127.0.0.1", args.port), engine)
    except OSError:
        print(f"端口 {args.port} 无法使用。请尝试 --port {args.port + 1}，不要停止其他程序。", file=sys.stderr)
        return 1
    print(f"抖音内容分析研究引擎 v{__version__}\n本机访问：http://127.0.0.1:{args.port}\nCtrl+C 停止；不自动保存用户输入。", flush=True)
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
