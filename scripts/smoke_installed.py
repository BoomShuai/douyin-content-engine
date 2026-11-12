"""Run with python -I after installing the package to check real bundled resources."""
import http.client
import importlib.resources
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from douyin_engine.engine.pipeline import AnalysisEngine
from douyin_engine.server import LocalServer


def main():
    resources = importlib.resources.files("douyin_engine")
    records = json.loads(resources.joinpath("data/demo.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as directory:
        result = AnalysisEngine(Path(directory) / "missing-model").analyze({"records": records, "backend": "rules", "source_name": "synthetic_demo"})
        assert result["summary"]["total"] == 36
        server = LocalServer(("127.0.0.1", 0), AnalysisEngine())
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            connection.request("GET", "/")
            response = connection.getresponse()
            assert response.status == 200 and b"/app.js" in response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            assert not thread.is_alive()
        fixture = Path(directory) / "synthetic.json"
        fixture.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        output = Path(directory) / "result.json"
        process = subprocess.run([sys.executable, "-I", "-m", "douyin_engine", "analyze", str(fixture), "--source-name", "synthetic_demo", "--output", str(output)], cwd=directory, capture_output=True)
        assert process.returncode == 0, "Installed CLI failed"
        assert json.loads(output.read_text(encoding="utf-8"))["summary"]["total"] == 36
    print("PASS: installed package, synthetic analysis, HTTP assets and CLI outside source tree")


if __name__ == "__main__":
    main()
