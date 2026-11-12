import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from douyin_engine.__main__ import main
from scripts import build_release, verify_release

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publication_privacy", ROOT / "scripts/privacy_check.py")
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


class PublicProjectTests(unittest.TestCase):
    def test_cli_handles_a_non_utf8_console(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="ascii", write_through=True)
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["doctor"]), 0)
            self.assertIn("规则模式", buffer.getvalue().decode("utf-8"))
        finally:
            stream.detach()

    def test_release_rejects_private_content_in_approved_document(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            fixture = base / "source"
            for relative in build_release.REQUIRED_FILES:
                path = fixture / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic fixture\n", encoding="utf-8")
            (fixture / "README.md").write_text("private" + "@" + "mail.example", encoding="utf-8")
            with self.assertRaisesRegex(build_release.ReleaseError, "privacy check"):
                build_release.build_release(fixture, base / "must-not-exist.zip")
            self.assertFalse((base / "must-not-exist.zip").exists())

    def test_privacy_patterns_and_reserved_synthetic_emails(self):
        for text in ["ghp" + "_" + "A" * 36, "person" + "@" + "mail.example", "/" + "Users" + "/person/project", "138" + "1234" + "5678"]:
            self.assertTrue(privacy.scan(text))
        self.assertEqual(privacy.scan("person" + "@" + "example.com"), [])
        self.assertTrue(privacy.inspect("models/config.json", b"{}"))
        self.assertTrue(privacy.inspect("artifacts/results.json", b"{}"))

    def test_doctor_accepts_missing_optional_model(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["doctor"]), 0)
        data = json.loads(output.getvalue())
        self.assertTrue(data["core_available"])
        self.assertFalse(data["model_bundled"])

    def test_cli_analyze_defaults_to_rules_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "demo.json"
            args = ["analyze", str(ROOT / "douyin_engine/data/demo.json"), "--output", str(output)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
            saved = output.read_bytes()
            self.assertEqual(json.loads(saved)["backend"], "rules")
            self.assertEqual(json.loads(saved)["summary"]["total"], 36)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(args)
            self.assertEqual(output.read_bytes(), saved)

    def test_source_zip_verification_and_relocation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            fixture = base / "source"
            for relative in build_release.REQUIRED_FILES:
                path = fixture / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic fixture\n", encoding="utf-8")
            archive = base / "source.zip"
            build_release.build_release(fixture, archive)
            verified = verify_release.verify_archive(archive)
            self.assertTrue(verified["all_file_hashes_verified"])
            self.assertFalse(verified["manifest"]["release"]["model_included"])
            extracted = verify_release.extract_verified(archive, base / "new-location", verified)
            self.assertEqual((extracted / "README.md").read_text(), "synthetic fixture\n")
            changed = base / "tampered.zip"
            with zipfile.ZipFile(archive) as source, zipfile.ZipFile(changed, "w") as target:
                for item in source.infolist():
                    target.writestr(item, b"tampered" if item.filename == "README.md" else source.read(item))
            with self.assertRaises(verify_release.VerificationError):
                verify_release.verify_archive(changed)


if __name__ == "__main__":
    unittest.main()
