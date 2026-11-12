"""Opt-in tests of the actual frozen CPU model, never benchmark accuracy tests.

Run with RUN_MODEL_TESTS=1. Socket connection and DNS functions are blocked for
the entire class, including the one real weight load. No predictions are mocked.
"""

import gc
import math
import os
import unittest
from unittest.mock import patch

from douyin_engine.engine.analytics import analyze_records
from douyin_engine.engine.errors import EngineError
from douyin_engine.engine.sarcasm import SarcasmModel, encode_input


MODEL_TEST_EVIDENCE = {
    "requested": os.environ.get("RUN_MODEL_TESTS") == "1",
    "real_weights_loaded": False,
    "device": None,
    "network_calls_blocked": [],
    "fixture_note": "Synthetic operation fixtures; no accuracy or ground-truth claim.",
}


@unittest.skipUnless(os.environ.get("RUN_MODEL_TESTS") == "1", "Real offline model tests require RUN_MODEL_TESTS=1 and user-provided compatible weights")
class OfflineModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def forbidden(kind):
            def reject(*args, **kwargs):
                MODEL_TEST_EVIDENCE["network_calls_blocked"].append(kind)
                raise AssertionError("Network access is forbidden in real offline model tests")
            return reject

        for name in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection", "socket.getaddrinfo"):
            blocker = patch(name, side_effect=forbidden(name))
            blocker.start()
            cls.addClassCleanup(blocker.stop)
        environment = patch.dict(os.environ, {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1"})
        environment.start()
        cls.addClassCleanup(environment.stop)
        cls.classifier = SarcasmModel(device="cpu", batch_size=16)
        cls.addClassCleanup(cls.release_model)
        cls.classifier.load()
        MODEL_TEST_EVIDENCE.update(real_weights_loaded=True, device=cls.classifier.device,
                                   model_name=cls.classifier.status()["name"])
        cls.rows = [
            {"id": "cpu-fixture-1", "text": "服务人员认真解决了问题，谢谢。", "context": "配送售后"},
            {"id": "cpu-fixture-2", "text": "等了三天都没有送到，可真是优秀的服务。", "context": "订单延误"},
            {"id": "cpu-fixture-3", "text": "今天收到包裹，包装完整。", "context": "收货记录"},
        ]
        cls.inputs = [encode_input(row["text"], row["context"]) for row in cls.rows]
        cls.batch_scores = cls.classifier.predict_scores(cls.inputs)
        cls.single_scores = [cls.classifier.predict_scores([text])[0] for text in cls.inputs]
        cls.annotated = cls.classifier.annotate(cls.rows)
        MODEL_TEST_EVIDENCE["fixture_count"] = len(cls.rows)
        MODEL_TEST_EVIDENCE["batch_scores"] = cls.batch_scores
        MODEL_TEST_EVIDENCE["single_scores"] = cls.single_scores
        MODEL_TEST_EVIDENCE["batch_single_max_absolute_difference"] = max(
            abs(batch - single) for batch, single in zip(cls.batch_scores, cls.single_scores)
        )

    @classmethod
    def release_model(cls):
        if getattr(cls, "classifier", None) is not None:
            cls.classifier.model = cls.classifier.tokenizer = cls.classifier.torch = None
            cls.classifier = None
        gc.collect()

    def test_actual_cpu_weights_and_eval_mode(self):
        status = self.classifier.status()
        self.assertEqual(status["status"], "loaded")
        self.assertEqual(status["device"], "cpu")
        self.assertTrue(status["weights_present"])
        self.assertTrue(status["dependencies_present"])
        self.assertEqual(self.classifier.model.config.num_labels, 2)
        self.assertFalse(self.classifier.model.training)

    def test_empty_list_never_loads_another_model(self):
        fresh = SarcasmModel(device="cpu")
        with patch.object(fresh, "load", side_effect=AssertionError("Empty input must not load weights")):
            self.assertEqual(fresh.predict_scores([]), [])
        self.assertIsNone(fresh.model)

    def test_actual_predictions_are_finite_bounded_and_complete(self):
        self.assertEqual(len(self.batch_scores), len(self.inputs))
        for score in self.batch_scores:
            self.assertTrue(math.isfinite(score))
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

    def test_batch_and_single_predictions_agree(self):
        self.assertEqual(len(self.batch_scores), len(self.single_scores))
        for batch, single in zip(self.batch_scores, self.single_scores):
            self.assertLessEqual(abs(batch - single), 1e-5)

    def test_annotations_preserve_model_scores_threshold_and_required_review(self):
        self.assertEqual(len(self.annotated), len(self.rows))
        for row, expected in zip(self.annotated, self.batch_scores):
            output = row["sarcasm"]
            self.assertEqual(output["backend"], "model")
            self.assertTrue(output["available"])
            self.assertTrue(output["review_required"])
            self.assertEqual(output["label"], int(output["score"] >= 0.5))
            self.assertLessEqual(abs(output["score"] - expected), 1e-5)
            self.assertIn(output["review_priority"], ("normal", "high"))

    def test_unscorable_inputs_abstain_and_do_not_infer(self):
        rows = [{"id": "empty-clean", "text": "https://example.invalid", "context": "配送"},
                {"id": "hidden-comment", "text": "服务好", "context": "语境" * 130}]
        with patch.object(self.classifier, "load", side_effect=AssertionError("Abstained inputs must not invoke inference")):
            output = self.classifier.annotate(rows)
        for row in output:
            self.assertIsNone(row["sarcasm"]["label"])
            self.assertIsNone(row["sarcasm"]["score"])
            self.assertFalse(row["sarcasm"]["available"])
            self.assertTrue(row["sarcasm"]["review_required"])
        summary = analyze_records(output)["summary"]
        self.assertEqual(summary["unscorable_count"], 2)
        self.assertEqual(summary["sarcasm_scored_count"], 0)
        self.assertEqual(summary["sarcasm_count"], 0)

    def test_non_string_model_input_is_rejected(self):
        with self.assertRaises(EngineError) as error:
            self.classifier.predict_scores([None])
        self.assertEqual(error.exception.code, "INVALID_MODEL_INPUT")

    def test_no_network_attempts_during_load_or_inference(self):
        self.assertEqual(MODEL_TEST_EVIDENCE["network_calls_blocked"], [])


if __name__ == "__main__":
    unittest.main()
