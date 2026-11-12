"""Behavioral/adversarial tests for the delivery analytics contract."""

import copy
import math
import unittest

from douyin_engine.engine.analytics import analyze_records


def row(record_id="c1", text="服务很好", author="a", timestamp="2026-08-01T10:00:00+08:00", **overrides):
    value = {
        "id": record_id, "text": text, "context": "", "author_id": author,
        "published_at": timestamp, "video_id": "v1", "parent_id": None,
        "likes": 0, "reply_count": 0, "source": "test_fixture",
        "sarcasm": {"label": 0, "score": None, "review_required": False, "backend": "rules", "reasons": []},
    }
    value.update(overrides)
    return value


class SentimentTests(unittest.TestCase):
    def sentiment(self, text):
        return analyze_records([row(text=text)])["comments"][0]["sentiment"]

    def test_positive_negative_neutral(self):
        for text, expected in [("产品好用", "positive"), ("质量糟糕", "negative"), ("我今天收到包裹", "neutral")]:
            with self.subTest(text=text):
                self.assertEqual(self.sentiment(text)["label"], expected)

    def test_negation_and_double_negation(self):
        for text, expected in [
            ("我不推荐", "negative"), ("我不太喜欢", "negative"),
            ("质量并不好", "negative"), ("服务不是不好", "positive"),
            ("我并不讨厌", "positive"), ("质量不错", "positive"),
            ("这不是不错", "negative"), ("我不是不满意", "positive"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.sentiment(text)["label"], expected)

    def test_non_negating_compounds(self):
        for text in ["质量不但好而且漂亮", "没想到质量好", "产品无比好用", "不得不说服务好"]:
            with self.subTest(text=text):
                self.assertEqual(self.sentiment(text)["label"], "positive")

    def test_negation_does_not_cross_clause_or_previous_token(self):
        result = self.sentiment("不满意，服务好")
        self.assertEqual([match["polarity"] for match in result["matches"]], [-1, 1])
        result = self.sentiment("不满意质量差")
        self.assertEqual([match["polarity"] for match in result["matches"]], [-1, -1])

    def test_contrast_uses_latter_clause(self):
        result = self.sentiment("质量好，但是服务差")
        self.assertEqual(result["label"], "negative")
        self.assertEqual([match["weight"] for match in result["matches"]], [1.0, 1.5])

    def test_protected_substrings_are_not_sentiment(self):
        for text in ["快递到了", "我在贵州", "存在时差", "差不多", "我很好奇", "贵公司", "发一个快照"]:
            with self.subTest(text=text):
                self.assertEqual(self.sentiment(text)["label"], "neutral")

    def test_sarcasm_does_not_flip_literal_sentiment(self):
        a = row(text="服务真好")
        b = copy.deepcopy(a)
        b["sarcasm"].update(label=1, score=0.99, review_required=True)
        first = analyze_records([a])
        second = analyze_records([b])
        self.assertEqual(first["comments"][0]["sentiment"], second["comments"][0]["sentiment"])
        self.assertEqual(second["summary"]["sarcasm_count"], 1)
        self.assertEqual(second["summary"]["review_count"], 1)

    def test_unscorable_records_not_counted_as_non_sarcastic(self):
        known = row("known")
        positive = row("positive")
        positive["sarcasm"]["label"] = 1
        unknown = row("unknown")
        unknown["sarcasm"].update(label=None, score=None, available=False, review_required=True)
        result = analyze_records([known, positive, unknown])
        for summary in (result["summary"], result["timeline"][0]):
            self.assertEqual(summary["sarcasm_count"], 1)
            self.assertEqual(summary["sarcasm_scored_count"], 2)
            self.assertEqual(summary["unscorable_count"], 1)
        self.assertIsNone(result["comments"][2]["sarcasm"]["label"])

    def test_empty_text_no_opinions_and_finite_score(self):
        result = analyze_records([row(text="")])
        self.assertEqual(result["comments"][0]["opinions"], [])
        self.assertEqual(result["comments"][0]["sentiment"]["score"], 0.0)

    def test_scores_bounded(self):
        for text in ["好" * 1000, "差" * 1000, "好差" * 500, "很好但是非常差"]:
            score = self.sentiment(text)["score"]
            self.assertTrue(math.isfinite(score))
            self.assertGreaterEqual(score, -1)
            self.assertLessEqual(score, 1)

    def test_repeated_protected_compounds_remain_neutral(self):
        self.assertEqual(self.sentiment("贵州快递时差" * 1000)["label"], "neutral")


class OpinionTests(unittest.TestCase):
    def test_all_evidence_spans_are_exact_unicode_substrings(self):
        text = "🙂  服务不太好，  质量不错！<script>alert(1)</script>"
        comment = analyze_records([row(text=text)])["comments"][0]
        self.assertTrue(comment["opinions"])
        for opinion in comment["opinions"]:
            self.assertEqual(text[opinion["start"]:opinion["end"]], opinion["evidence"])
            self.assertIn(opinion["opinion"], opinion["evidence"])
            self.assertGreaterEqual(opinion["start"], 0)
            self.assertGreater(opinion["end"], opinion["start"])
            self.assertTrue(opinion["review_required"])
        for match in comment["sentiment"]["matches"]:
            self.assertEqual(text[match["start"]:match["end"]], match["evidence"])

    def test_known_target_and_holder(self):
        opinion = analyze_records([row(text="服务很好", author="batch_author_1")])["comments"][0]["opinions"][0]
        self.assertEqual(opinion["target"], "服务")
        self.assertEqual(opinion["holder"], "batch_author_1")

    def test_unknown_target_not_inferred_from_context(self):
        opinion = analyze_records([row(text="它很好", author=None, context="手机服务产品")])["comments"][0]["opinions"][0]
        self.assertIsNone(opinion["target"])
        self.assertIsNone(opinion["holder"])

    def test_quoted_other_speaker_not_assigned_to_commenter(self):
        opinion = analyze_records([row(text="有人说服务很好", author="a")])["comments"][0]["opinions"][0]
        self.assertIsNone(opinion["holder"])

    def test_context_not_sentiment_evidence(self):
        comment = analyze_records([row(text="收到包裹", context="质量太差真垃圾")])["comments"][0]
        self.assertEqual(comment["sentiment"]["label"], "neutral")
        self.assertEqual(comment["opinions"], [])

    def test_multiple_targets_stay_in_own_clauses(self):
        opinions = analyze_records([row(text="质量好，但是物流差")])["comments"][0]["opinions"]
        self.assertEqual([(op["target"], op["sentiment"]) for op in opinions], [("质量", "positive"), ("物流", "negative")])


class TimelineTests(unittest.TestCase):
    def test_timezone_bucket_conversion(self):
        result = analyze_records([
            row("a", timestamp="2026-08-01T16:30:00Z"),
            row("b", timestamp="2026-08-02T00:45:00+08:00"),
            row("c", timestamp="2026-08-01T09:40:00-07:00"),
        ], bucket="hour")
        self.assertEqual(len(result["timeline"]), 1)
        self.assertEqual(result["timeline"][0]["time"], "2026-08-02T00:00:00+08:00")
        self.assertEqual(result["timeline"][0]["count"], 3)

    def test_missing_naive_bad_dates_excluded_not_imputed(self):
        result = analyze_records([row("a", timestamp=None), row("b", timestamp="nope"),
                                  row("c", timestamp="2026-08-01T10:00:00"), row("d")])
        self.assertEqual(result["summary"]["total"], 4)
        self.assertEqual(result["summary"]["dated_count"], 1)
        self.assertEqual(sum(bucket["count"] for bucket in result["timeline"]), 1)
        self.assertTrue(any("3条" in warning for warning in result["warnings"]))

    def test_all_missing_dates_no_timeline(self):
        result = analyze_records([row(timestamp=None)])
        self.assertEqual(result["timeline"], [])
        self.assertEqual(result["turning_points"], [])

    def test_empty_buckets_retained(self):
        result = analyze_records([row("a", timestamp="2026-08-01T10:00:00+08:00"),
                                  row("b", timestamp="2026-08-03T10:00:00+08:00")])
        self.assertEqual([item["count"] for item in result["timeline"]], [1, 0, 1])
        empty = result["timeline"][1]
        self.assertTrue(empty["is_empty"])
        self.assertEqual(empty["sentiment_mean"], 0)
        self.assertEqual(empty["representative_ids"], [])

    def test_long_gap_is_explicit_and_bounded(self):
        result = analyze_records([row("a", timestamp="2000-01-01T00:00:00+08:00"),
                                  row("b", timestamp="2026-08-03T10:00:00+08:00")], bucket="hour")
        self.assertEqual(len(result["timeline"]), 2)
        self.assertGreater(result["timeline"][1]["gap_before"]["empty_buckets"], 2000)
        self.assertTrue(any("gap_before" in warning for warning in result["warnings"]))

    def test_polarity_counts_and_mean_variance_conservation(self):
        result = analyze_records([row("p", text="好"), row("n", text="差"), row("z", text="收到")])
        summary = result["summary"]
        self.assertEqual(summary["total"], summary["positive_count"] + summary["negative_count"] + summary["neutral_count"])
        item = result["timeline"][0]
        scores = [record["sentiment"]["score"] for record in result["comments"]]
        self.assertEqual(item["sentiment_mean"], 0.0)
        self.assertAlmostEqual(item["sentiment_mean"], sum(scores) / 3, places=7)
        self.assertAlmostEqual(item["sentiment_variance"], sum(score ** 2 for score in scores) / 3, places=7)
        self.assertEqual(item["count"], item["positive_count"] + item["negative_count"] + item["neutral_count"])

    def test_population_variance_for_single_sample_is_zero(self):
        self.assertEqual(analyze_records([row()])["timeline"][0]["sentiment_variance"], 0)

    def test_turning_points_need_adjacent_sufficient_samples(self):
        rows = [row("a%d" % index, text="质量好", timestamp="2026-08-01T10:00:00+08:00") for index in range(3)]
        rows += [row("b%d" % index, text="质量差", timestamp="2026-08-02T10:00:00+08:00") for index in range(3)]
        result = analyze_records(rows)
        self.assertEqual(len(result["turning_points"]), 1)
        self.assertEqual(result["turning_points"][0]["direction"], "more_negative")
        self.assertIn("不能", result["turning_points"][0]["description"])
        rows[-1]["published_at"] = None
        self.assertEqual(analyze_records(rows)["turning_points"], [])

    def test_no_turning_point_across_empty_or_long_gap(self):
        for later in ["2026-08-03T10:00:00+08:00", "2036-08-03T10:00:00+08:00"]:
            rows = [row("a%d" % i, text="好", timestamp="2026-08-01T10:00:00+08:00") for i in range(3)]
            rows += [row("b%d" % i, text="差", timestamp=later) for i in range(3)]
            self.assertEqual(analyze_records(rows)["turning_points"], [])

    def test_bad_bucket_rejected(self):
        with self.assertRaises(ValueError):
            analyze_records([], bucket="minute")

    def test_datetime_max_does_not_overflow_bucket_iteration(self):
        result = analyze_records([row(timestamp="9999-12-31T23:00:00+08:00")], bucket="hour")
        self.assertEqual(result["timeline"][0]["count"], 1)


class NetworkTests(unittest.TestCase):
    def test_no_edge_pagerank_uniform_and_no_fabricated_edge(self):
        result = analyze_records([row("a", author="a", reply_count=100), row("b", author="b")])["network"]
        self.assertEqual(result["edges"], [])
        self.assertTrue(all(abs(node["pagerank"] - 0.5) < 1e-10 for node in result["nodes"]))
        self.assertTrue(any("没有可验证" in warning for warning in result["warnings"]))

    def test_reply_direction_and_weights(self):
        network = analyze_records([row("root", author="a"), row("r1", author="b", parent_id="root"),
                                   row("r2", author="b", parent_id="root"), row("r3", author="c", parent_id="root")])["network"]
        self.assertEqual(network["edges"], [{"source": "b", "target": "a", "weight": 2},
                                            {"source": "c", "target": "a", "weight": 1}])
        nodes = {node["id"]: node for node in network["nodes"]}
        self.assertEqual(nodes["a"]["in_degree"], 2)
        self.assertEqual(nodes["a"]["in_reply_count"], 3)
        self.assertEqual(nodes["b"]["out_degree"], 1)
        self.assertEqual(nodes["b"]["out_reply_count"], 2)
        self.assertGreater(nodes["a"]["pagerank"], nodes["b"]["pagerank"])
        self.assertAlmostEqual(sum(node["pagerank"] for node in nodes.values()), 1.0, places=10)

    def test_unknown_author_and_dangling_parent_not_created(self):
        network = analyze_records([row("root", author=None), row("r1", author="b", parent_id="root"),
                                   row("r2", author="c", parent_id="absent"), row("r3", author=None, parent_id="r2")])["network"]
        self.assertEqual(network["edges"], [])
        self.assertEqual({node["id"] for node in network["nodes"]}, {"b", "c"})
        self.assertEqual(network["unknown_author_count"], 2)
        self.assertEqual(network["missing_parent_count"], 1)
        self.assertEqual(network["unavailable_author_edge_count"], 2)

    def test_same_author_reply_and_direct_self_reference_excluded(self):
        network = analyze_records([row("root", author="a"), row("r1", author="a", parent_id="root"),
                                   row("r2", author="b", parent_id="r2")])["network"]
        self.assertEqual(network["edges"], [])
        self.assertEqual(network["self_reply_excluded_count"], 2)

    def test_ambiguous_duplicate_parent_id_not_linked(self):
        network = analyze_records([row("same", author="a"), row("same", author="b"),
                                   row("reply", author="c", parent_id="same")])["network"]
        self.assertEqual(network["edges"], [])
        self.assertEqual(network["missing_parent_count"], 1)

    def test_isolated_nodes_and_cycles_stay_normalized(self):
        rows = [row("a", author="a", parent_id="b"), row("b", author="b", parent_id="a"), row("c", author="c")]
        network = analyze_records(rows)["network"]
        ranks = {node["id"]: node["pagerank"] for node in network["nodes"]}
        self.assertAlmostEqual(sum(ranks.values()), 1, places=10)
        self.assertAlmostEqual(ranks["a"], ranks["b"], places=10)
        self.assertGreater(ranks["a"], ranks["c"])
        self.assertTrue(network["pagerank_converged"])

    def test_composite_scores_finite_and_bounded(self):
        network = analyze_records([row("a", author="a", likes=10 ** 1000), row("b", author="b", likes=-1)])["network"]
        for node in network["nodes"]:
            self.assertTrue(math.isfinite(node["score"]))
            self.assertGreaterEqual(node["score"], 0)
            self.assertLessEqual(node["score"], 100)

    def test_weighted_pagerank_respects_relative_edge_weights(self):
        network = analyze_records([
            row("b", author="b"), row("c", author="c"),
            row("ab1", author="a", parent_id="b"),
            row("ab2", author="a", parent_id="b"),
            row("ac", author="a", parent_id="c"),
        ])["network"]
        ranks = {node["id"]: node["pagerank"] for node in network["nodes"]}
        self.assertGreater(ranks["b"], ranks["c"])
        self.assertGreater(ranks["c"], ranks["a"])


class ContractTests(unittest.TestCase):
    def test_empty_input_has_full_schema(self):
        result = analyze_records([])
        self.assertEqual(set(result), {"summary", "comments", "timeline", "turning_points", "topics", "network", "warnings", "methodology"})
        self.assertTrue(all(value == 0 for value in result["summary"].values()))
        self.assertEqual(result["network"]["nodes"], [])
        self.assertEqual(result["network"]["edges"], [])

    def test_does_not_mutate_source_records(self):
        rows = [row(), row("b", text="不满意", author=None)]
        before = copy.deepcopy(rows)
        analyze_records(rows)
        self.assertEqual(rows, before)

    def test_topics_count_once_per_comment_and_allow_context(self):
        result = analyze_records([row("a", text="客服服务售后很好", context="物流"), row("b", text="服务差")])
        topics = {topic["term"]: topic for topic in result["topics"]}
        self.assertEqual(topics["服务与售后"]["count"], 2)
        self.assertEqual(topics["服务与售后"]["comment_ids"], ["a", "b"])
        self.assertEqual(topics["物流与配送"]["count"], 1)

    def test_unknown_topic_not_invented(self):
        result = analyze_records([row(text="今天星期三", context="")])
        self.assertEqual(result["topics"], [])
        self.assertEqual(result["summary"]["topic_covered_count"], 0)

    def test_invalid_public_inputs_rejected(self):
        for value in [None, "abc", {"text": "好"}, [None]]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    analyze_records(value)
        with self.assertRaises(ValueError):
            analyze_records([row(text=None)])


if __name__ == "__main__":
    unittest.main()
