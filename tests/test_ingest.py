SYNTHETIC_PHONE = '138' + '1234' + '5678'
'Input contract and privacy tests; all records are synthetic fixtures.'
import csv
import io
import json
import unittest
from douyin_engine.engine.errors import EngineError
from douyin_engine.engine.ingest import MAX_BYTES, MAX_ROWS, MAX_TEXT, normalize_records, parse_content, parse_timestamp, safe_csv_cell, template_csv

class ParseContentTests(unittest.TestCase):

    def test_bom_csv_quotes_commas_and_embedded_newlines(self):
        content = '\ufeffid,text,context\r\nc1,"包装""很好"",服务不错\n下次再来","配送,体验"\r\n'
        raw = parse_content(content, 'csv')
        self.assertEqual(raw[0]['text'], '包装"很好",服务不错\n下次再来')
        (rows, _) = normalize_records(raw)
        self.assertEqual(rows[0]['context'], '配送,体验')
        self.assertIn('\n', rows[0]['text'])

    def test_json_array_and_supported_wrappers(self):
        fixture = [{'id': 'a', 'text': '人工构造评论'}]
        for payload in (fixture, {'records': fixture}, {'comments': fixture}):
            with self.subTest(payload=payload):
                self.assertEqual(parse_content('\ufeff' + json.dumps(payload), 'json'), fixture)

    def test_nonfinite_invalid_and_deep_json_rejected(self):
        values = ['[{"text":"x","likes":NaN}]', '[{"text":"x","likes":Infinity}]', '[{"text":"x","likes":-Infinity}]', '{"records":', '[' * 3000 + ']' * 3000]
        for content in values:
            with self.subTest(content=content[:50]), self.assertRaises(EngineError) as error:
                parse_content(content, 'json')
            self.assertEqual(error.exception.code, 'PARSE_ERROR')

    def test_non_array_json_rejected(self):
        for content in ('null', '1', '"x"', '{}', '{"records":{}}'):
            with self.subTest(content=content), self.assertRaises(EngineError):
                parse_content(content, 'json')

    def test_bad_csv_header_and_extra_columns(self):
        for content in ('id,text,text\na,x,y', 'id,likes\na,1', ''):
            with self.subTest(content=content), self.assertRaises(EngineError) as error:
                parse_content(content, 'csv')
            self.assertEqual(error.exception.code, 'INVALID_HEADER')
        with self.assertRaises(EngineError) as error:
            parse_content('id,text\na,x,unexpected', 'csv')
        self.assertEqual(error.exception.code, 'CSV_COLUMNS')

    def test_unclosed_csv_quote_rejected(self):
        with self.assertRaises(EngineError) as error:
            parse_content('id,text\na,"unfinished', 'csv')
        self.assertEqual(error.exception.code, 'PARSE_ERROR')

    def test_byte_and_csv_row_limits(self):
        with self.assertRaises(EngineError) as error:
            parse_content('x' * (MAX_BYTES + 1), 'csv')
        self.assertEqual(error.exception.status, 413)
        csv_text = 'text\n' + 'fixture\n' * (MAX_ROWS + 1)
        with self.assertRaises(EngineError) as error:
            parse_content(csv_text, 'csv')
        self.assertEqual(error.exception.status, 413)

    def test_invalid_content_and_format(self):
        for (content, fmt) in ((None, 'json'), (b'[]', 'json'), ('[]', 'pickle')):
            with self.subTest(fmt=fmt), self.assertRaises(EngineError):
                parse_content(content, fmt)

    def test_template_is_bom_csv_roundtrip(self):
        template = template_csv()
        self.assertTrue(template.startswith('\ufeff'))
        (rows, _) = normalize_records(parse_content(template, 'csv'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], 'example-1')

class NormalizationTests(unittest.TestCase):

    def test_aliases_are_canonicalized_and_author_is_pseudonymous(self):
        raw = [{'cid': 123, 'comment': '服务很好', 'video_title': '配送主题', 'user_id': 'PRIVATE_USER', 'created_at': '2026-08-31T09:00:00Z', 'aweme_id': 321, 'reply_to': 456, 'digg_count': '5', 'reply_comment_total': 2}]
        (rows, report) = normalize_records(raw, source_name='fixture')
        self.assertEqual(set(rows[0]), {'id', 'text', 'context', 'author_id', 'published_at', 'video_id', 'parent_id', 'likes', 'reply_count', 'source'})
        self.assertEqual(rows[0]['id'], '123')
        self.assertEqual(rows[0]['video_id'], '321')
        self.assertEqual(rows[0]['parent_id'], '456')
        self.assertEqual(rows[0]['likes'], 5)
        self.assertEqual(rows[0]['reply_count'], 2)
        self.assertEqual(rows[0]['published_at'], '2026-08-31T17:00:00+08:00')
        self.assertNotEqual(rows[0]['author_id'], 'PRIVATE_USER')
        self.assertEqual(report['accepted'], 1)

    def test_optional_missing_fields_not_fabricated(self):
        (rows, report) = normalize_records([{'text': '只有评论'}])
        self.assertIsNone(rows[0]['author_id'])
        self.assertIsNone(rows[0]['published_at'])
        self.assertIsNone(rows[0]['video_id'])
        self.assertIsNone(rows[0]['parent_id'])
        self.assertEqual(rows[0]['context'], '')
        self.assertEqual(report['missing'], {'time': 1, 'context': 1, 'author': 1})
        self.assertEqual(report['assumptions']['GENERATED_COMMENT_ID'], 1)

    def test_generated_ids_do_not_collide_with_explicit_ids(self):
        (rows, _) = normalize_records([{'text': 'missing id'}, {'id': 'auto-1', 'text': 'explicit id'}])
        self.assertEqual(len({row['id'] for row in rows}), 2)
        self.assertEqual(rows[1]['id'], 'auto-1')

    def test_same_author_maps_consistently_within_batch(self):
        (rows, _) = normalize_records([{'text': 'a', 'user_id': 'same'}, {'text': 'b', 'user_id': 'other'}, {'text': 'c', 'user_id': 'same'}])
        self.assertEqual(rows[0]['author_id'], rows[2]['author_id'])
        self.assertNotEqual(rows[0]['author_id'], rows[1]['author_id'])

    def test_duplicate_identical_record_is_counted_separately_from_conflict(self):
        first = {'id': 'a', 'text': '服务好', 'author_id': 'original-author'}
        (rows, report) = normalize_records([first, dict(first), {'id': 'a', 'text': '质量差'}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['text'], '服务好')
        self.assertEqual(report['duplicates'], 1)
        self.assertEqual(report['rejected'], 1)
        self.assertEqual({issue['code'] for issue in report['issues']}, {'DUPLICATE_ID', 'CONFLICTING_ID'})

    def test_bad_rows_rejected_with_row_numbers_without_erasing_valid_rows(self):
        (rows, report) = normalize_records([{'text': 'valid'}, {}, {'text': ' '}, None, {'text': 123}, {'text': 'x', 'id': []}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(report['rejected'], 5)
        self.assertEqual([issue['row'] for issue in report['issues']], [2, 3, 4, 5, 6])

    def test_empty_and_all_invalid_inputs_fail_explicitly(self):
        for records in ([], None, {}, [None], [{'text': ''}]):
            with self.subTest(records=records), self.assertRaises(EngineError):
                normalize_records(records)

    def test_row_text_and_total_byte_limits(self):
        with self.assertRaises(EngineError) as error:
            normalize_records([{'text': 'x'}] * (MAX_ROWS + 1))
        self.assertEqual(error.exception.status, 413)
        (rows, report) = normalize_records([{'text': 'valid'}, {'text': 'x' * (MAX_TEXT + 1)}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(report['issues'][0]['code'], 'TEXT_TOO_LONG')
        with self.assertRaises(EngineError) as error:
            normalize_records([{'text': 'x' * (MAX_BYTES + 1)}])
        self.assertEqual(error.exception.status, 413)

    def test_nonfinite_record_rejected_before_analysis(self):
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value), self.assertRaises(EngineError) as error:
                normalize_records([{'text': 'valid', 'likes': value}])
            self.assertEqual(error.exception.code, 'INVALID_RECORDS')

    def test_invalid_counts_are_zero_and_reported(self):
        for value in (-1, 'bad', 1.5, True, 10 ** 13, []):
            with self.subTest(value=value):
                (rows, report) = normalize_records([{'text': 'fixture', 'likes': value}])
                self.assertEqual(rows[0]['likes'], 0)
                self.assertEqual(report['assumptions']['INVALID_COUNT_AS_ZERO'], 1)

    def test_bad_dates_keep_comment_but_have_no_timestamp(self):
        (rows, report) = normalize_records([{'text': 'fixture', 'published_at': '2026-02-30'}])
        self.assertIsNone(rows[0]['published_at'])
        self.assertEqual(report['assumptions']['INVALID_TIME'], 1)

    def test_date_only_does_not_invent_midnight(self):
        (stamp, note) = parse_timestamp('2026-08-31')
        self.assertIsNone(stamp)
        self.assertEqual(note, 'DATE_ONLY_TIME')

    def test_naive_datetime_assumption_is_declared(self):
        (stamp, note) = parse_timestamp('2026-08-31T09:30:00')
        self.assertEqual(stamp, '2026-08-31T09:30:00+08:00')
        self.assertEqual(note, 'ASSUMED_SHANGHAI_TIME')

    def test_timestamp_seconds_and_milliseconds_equivalent(self):
        self.assertEqual(parse_timestamp(1788163200), parse_timestamp(1788163200000))
        for value in (True, float('nan'), '1900-01-01T00:00:00Z', '2200-01-01T00:00:00Z', {}):
            with self.subTest(value=value):
                self.assertEqual(parse_timestamp(value), (None, 'INVALID_TIME'))

    def test_unknown_fields_and_common_pii_not_returned(self):
        raw = [{'id': 'c1', 'text': '电话' + SYNTHETIC_PHONE + ' 邮箱person@example.com 地址192.0.2.1', 'context': 'another@example.org', 'author_id': 'REAL_ACCOUNT_NAME', 'cookie': 'DO_NOT_LEAK_COOKIE', 'nickname': 'DO_NOT_LEAK_NICKNAME', 'ip_address': '198.51.100.7', 'arbitrary': {'secret': 'DO_NOT_LEAK_NESTED'}}]
        (rows, report) = normalize_records(raw)
        serialized = json.dumps({'rows': rows, 'report': report}, ensure_ascii=False)
        for secret in ('' + SYNTHETIC_PHONE + '', 'person@example.com', '192.0.2.1', 'another@example.org', 'REAL_ACCOUNT_NAME', 'DO_NOT_LEAK', '198.51.100.7'):
            self.assertNotIn(secret, serialized)
        self.assertGreaterEqual(report['pii_redactions'], 4)

    def test_sensitive_ids_are_opaque_and_parent_references_stay_consistent(self):
        (rows, report) = normalize_records([{'id': 'parent@example.com', 'text': 'root', 'video_id': '192.0.2.1'}, {'id': 'child', 'text': 'reply', 'parent_id': 'parent@example.com', 'video_id': '192.0.2.1'}])
        self.assertEqual(rows[0]['id'], rows[1]['parent_id'])
        self.assertEqual(rows[0]['video_id'], rows[1]['video_id'])
        serialized = json.dumps([rows, report])
        self.assertNotIn('parent@example.com', serialized)
        self.assertNotIn('192.0.2.1', serialized)

    def test_unknown_field_names_cannot_smuggle_pii_into_report(self):
        (rows, report) = normalize_records([{'text': 'fixture', 'person@example.com': 'ignored', 'COOKIE_SECRET_IN_KEY': 'ignored'}])
        serialized = json.dumps([rows, report])
        self.assertNotIn('person@example.com', serialized)
        self.assertNotIn('COOKIE_SECRET_IN_KEY', serialized)

    def test_source_override_sanitized_and_untrusted_source_field_not_used(self):
        (rows, _) = normalize_records([{'text': 'fixture', 'source': 'UNTRUSTED_SOURCE'}], 'phone' + SYNTHETIC_PHONE + '')
        self.assertNotIn('' + SYNTHETIC_PHONE + '', rows[0]['source'])
        self.assertNotEqual(rows[0]['source'], 'UNTRUSTED_SOURCE')

class CSVProtectionTests(unittest.TestCase):

    def test_formula_prefixes_are_neutralized(self):
        for value in ('=HYPERLINK("http://example.invalid")', '+1+1', '-1+1', '@SUM(1)', ' \t=1+1', '\r=1+1', '\n=1+1', '\tplain'):
            with self.subTest(value=value):
                self.assertTrue(safe_csv_cell(value).startswith("'"))

    def test_normal_values_are_preserved(self):
        for value in ('普通评论', 'x,y', '含"引号', '😀', '0'):
            self.assertEqual(safe_csv_cell(value), value)
        self.assertEqual(safe_csv_cell(None), '')
        self.assertEqual(safe_csv_cell(12), '12')
if __name__ == '__main__':
    unittest.main()
