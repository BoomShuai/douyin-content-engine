"""End-to-end local rules and mocked-model pipeline tests, not accuracy data."""

import copy
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from douyin_engine.engine.errors import EngineError
from douyin_engine.engine.pipeline import AnalysisEngine, export_csv


def fixture_rows():
    return [{"id": "c1", "text": "服务真好", "context": "配送延误", "author_id": "author-a", "published_at": "2026-08-31T09:00:00+08:00"},
            {"id": "c2", "text": "质量很好", "context": "产品体验", "author_id": "author-b", "parent_id": "c1", "published_at": "2026-08-31T10:00:00+08:00"}]


class PipelineTests(unittest.TestCase):
    def test_run_ids_do_not_collide_when_wall_clock_is_frozen(self):
        engine = AnalysisEngine()
        with patch("douyin_engine.engine.pipeline.time.time_ns", return_value=1):
            first = engine.analyze({"records": fixture_rows(), "backend": "rules"})
            second = engine.analyze({"records": fixture_rows(), "backend": "rules"})
        self.assertEqual(first["provenance"]["input_sha256"], second["provenance"]["input_sha256"])
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_default_backend_is_zero_dependency_rules(self):
        engine = AnalysisEngine()
        with patch.object(engine.model, "load", side_effect=AssertionError("default must not load weights")):
            result = engine.analyze({"records": fixture_rows()})
        self.assertEqual(result["backend"], "rules")
        self.assertTrue(all(row["sarcasm"]["score"] is None for row in result["comments"]))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="content-engine-model-missing-test-")
        self.addCleanup(self.temporary.cleanup)
        self.engine = AnalysisEngine(Path(self.temporary.name) / "absent-model")

    def test_rules_are_explicit_with_null_score_and_no_model_loading(self):
        with patch.object(self.engine.model, "load", side_effect=AssertionError("rules must not load model")):
            result = self.engine.analyze({"records": fixture_rows(), "backend": "rules", "bucket": "hour"})
        self.assertEqual(result["backend"], "rules")
        self.assertEqual(result["model"]["status"], "rules")
        self.assertEqual(result["summary"]["total"], 2)
        self.assertEqual(result["summary"]["review_count"], 2)
        for row in result["comments"]:
            self.assertIsNone(row["sarcasm"]["score"])
            self.assertEqual(row["sarcasm"]["backend"], "rules")
            self.assertTrue(row["sarcasm"]["review_required"])
        self.assertTrue(any("未运行" in warning for warning in result["warnings"]))
        self.assertEqual(result["provenance"]["bucket"], "hour")
        self.assertFalse(result["provenance"]["source_verified"])

    def test_explicit_model_missing_raises_without_rules_fallback(self):
        with patch("douyin_engine.engine.pipeline.annotate_rules") as rules:
            with self.assertRaises(EngineError) as error:
                self.engine.analyze({"records": fixture_rows(), "backend": "model"})
        self.assertEqual(error.exception.code, "MODEL_MISSING")
        self.assertEqual(error.exception.status, 503)
        rules.assert_not_called()

    def test_model_inference_failure_does_not_downgrade(self):
        with patch.object(self.engine.model, "annotate", side_effect=EngineError("MODEL_INFERENCE_FAILED", "fixture failure", 503)), patch("douyin_engine.engine.pipeline.annotate_rules") as rules:
            with self.assertRaises(EngineError) as error:
                self.engine.analyze({"records": fixture_rows(), "backend": "model"})
        self.assertEqual(error.exception.code, "MODEL_INFERENCE_FAILED")
        rules.assert_not_called()

    def test_mocked_model_scores_labels_and_review_are_preserved(self):
        with patch.object(self.engine.model, "predict_scores", return_value=[0.2, 0.8]) as predictor:
            result = self.engine.analyze({"records": fixture_rows(), "backend": "model"})
        self.assertEqual([row["sarcasm"]["label"] for row in result["comments"]], [0, 1])
        self.assertEqual([row["sarcasm"]["score"] for row in result["comments"]], [0.2, 0.8])
        self.assertTrue(all(row["sarcasm"]["review_required"] for row in result["comments"]))
        self.assertTrue(all(row["sarcasm"]["available"] for row in result["comments"]))
        self.assertEqual(result["backend"], "model")
        self.assertEqual(result["summary"]["sarcasm_scored_count"], 2)
        self.assertEqual(result["summary"]["unscorable_count"], 0)
        predictor.assert_called_once()

    def test_unscorable_text_does_not_become_non_sarcastic(self):
        rows = [{"id": "url", "text": "https://example.invalid/path", "context": "配送"},
                {"id": "long", "text": "服务好", "context": "上下文" * 100}]
        with patch.object(self.engine.model, "load", side_effect=AssertionError("unscorable inputs must not load the model")):
            result = self.engine.analyze({"records": rows, "backend": "model"})
        self.assertEqual(result["summary"]["unscorable_count"], 2)
        self.assertEqual(result["summary"]["sarcasm_scored_count"], 0)
        self.assertEqual(result["summary"]["sarcasm_count"], 0)
        for row in result["comments"]:
            self.assertIsNone(row["sarcasm"]["label"])
            self.assertIsNone(row["sarcasm"]["score"])
            self.assertFalse(row["sarcasm"]["available"])
            self.assertTrue(row["sarcasm"]["review_required"])

    def test_mixed_scorable_and_unscorable_preserves_record_order(self):
        rows = [{"id": "one", "text": "好", "context": "产品"}, {"id": "two", "text": "https://example.invalid"},
                {"id": "three", "text": "差", "context": "产品"}]
        with patch.object(self.engine.model, "predict_scores", return_value=[0.2, 0.8]) as predictor:
            result = self.engine.analyze({"records": rows, "backend": "model"})
        self.assertEqual([row["id"] for row in result["comments"]], ["one", "two", "three"])
        self.assertEqual([row["sarcasm"]["label"] for row in result["comments"]], [0, None, 1])
        self.assertEqual(len(predictor.call_args.args[0]), 2)
        self.assertEqual(result["summary"]["total"], result["summary"]["sarcasm_scored_count"] + result["summary"]["unscorable_count"])

    def test_review_band_priority_and_machine_label_are_independent(self):
        rows = [{"text": "服务好", "context": "配送"}, {"text": "质量差", "context": "产品"}]
        with patch.object(self.engine.model, "predict_scores", return_value=[0.5, 0.9]):
            result = self.engine.analyze({"records": rows, "backend": "model"})
        self.assertEqual([row["sarcasm"]["review_priority"] for row in result["comments"]], ["high", "normal"])
        self.assertEqual([row["sarcasm"]["label"] for row in result["comments"]], [1, 1])

    def test_imported_human_or_machine_annotations_do_not_override_predictions(self):
        raw = {"id": "a", "text": "收到包裹", "sarcasm": {"label": 1, "score": 1},
               "human_review": {"label": 1, "note": "UNVERIFIED_IMPORTED_REVIEW"}, "ground_truth": 1}
        original = copy.deepcopy(raw)
        result = self.engine.analyze({"records": [raw], "backend": "rules"})
        comment = result["comments"][0]
        self.assertEqual(comment["sarcasm"]["label"], 0)
        self.assertNotIn("human_review", comment)
        self.assertNotIn("ground_truth", comment)
        self.assertEqual(raw, original)
        self.assertIn("人工", result["provenance"]["review_note"])

    def test_preserving_separate_review_does_not_mutate_machine_csv_label(self):
        result = self.engine.analyze({"records": [{"text": "收到包裹"}], "backend": "rules"})
        original = copy.deepcopy(result["comments"][0]["sarcasm"])
        result["comments"][0]["human_review"] = {"label": 1, "note": "fixture", "status": "human_unverified"}
        exported = list(csv.DictReader(io.StringIO(export_csv(result).lstrip("\ufeff"))))
        self.assertEqual(exported[0]["sarcasm_prediction"], "0")
        self.assertEqual(result["comments"][0]["sarcasm"], original)

    def test_canonical_input_hash_stable_and_source_changes_are_tracked(self):
        first = self.engine.analyze({"records": fixture_rows(), "backend": "rules", "source_name": "fixture"})
        second = self.engine.analyze({"records": fixture_rows(), "backend": "rules", "source_name": "fixture"})
        changed = self.engine.analyze({"records": fixture_rows(), "backend": "rules", "source_name": "another"})
        self.assertEqual(first["provenance"]["input_sha256"], second["provenance"]["input_sha256"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertNotEqual(first["provenance"]["input_sha256"], changed["provenance"]["input_sha256"])
        self.assertEqual(len(first["provenance"]["input_sha256"]), 64)

    def test_csv_and_json_content_inputs(self):
        for content, fmt in (("id,text\na,服务好", "csv"), (json.dumps([{"id": "a", "text": "服务好"}]), "json")):
            with self.subTest(fmt=fmt):
                result = self.engine.analyze({"content": content, "format": fmt, "backend": "rules"})
                self.assertEqual(result["summary"]["total"], 1)

    def test_invalid_payloads_fail_explicitly(self):
        payloads = [None, [], {}, {"records": []}, {"records": fixture_rows(), "backend": "auto"},
                    {"records": fixture_rows(), "bucket": "week"}, {"records": fixture_rows(), "content": "[]"}]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(EngineError):
                self.engine.analyze(payload)

    def test_demo_disclaimer_and_date_only_exclusion(self):
        result = self.engine.analyze({"records": [{"text": "服务好", "published_at": "2026-08-31"}], "backend": "rules", "bucket": "hour", "source_name": "synthetic_demo"})
        self.assertEqual(result["summary"]["dated_count"], 0)
        self.assertEqual(result["timeline"], [])
        self.assertIn("人工构造", result["warnings"][0])

    def test_csv_export_protects_formula_and_retains_quotes_newlines(self):
        result = self.engine.analyze({"records": [{"id": "=2+2", "text": '=HYPERLINK("https://example.invalid")\n人工构造', "context": "@SUM(1)"}], "backend": "rules"})
        content = export_csv(result)
        self.assertTrue(content.startswith("\ufeff"))
        exported = list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
        self.assertTrue(exported[0]["id"].startswith("'="))
        self.assertTrue(exported[0]["text"].startswith("'="))
        self.assertTrue(exported[0]["context"].startswith("'@"))
        self.assertIn("\n", exported[0]["text"])
        self.assertEqual(exported[0]["sarcasm_score"], "")


if __name__ == "__main__":
    unittest.main()
