import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core

FIXED_NOW = datetime(2026, 10, 8, 12, tzinfo=core.TZ)


def event(stamp, incoming, outgoing, cached=0, reasoning=0):
    return json.dumps({'timestamp': stamp, 'type': 'event_msg', 'payload': {
        'type': 'token_count', 'info': {'total_token_usage': {
            'input_tokens': incoming, 'output_tokens': outgoing,
            'cached_input_tokens': cached, 'reasoning_output_tokens': reasoning,
            'total_tokens': incoming + outgoing}}}}) + '\n'


class LocalTokensTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / 'sessions').mkdir()
        (self.home / 'archived_sessions').mkdir()
        self.clock = patch('core.now', return_value=FIXED_NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.temp.cleanup()

    def write(self, name, text, folder='sessions'):
        p = self.home / folder / name
        p.write_text(text, encoding='utf-8')
        os.utime(p, (FIXED_NOW.timestamp(), FIXED_NOW.timestamp()))
        return p

    def test_duplicates_forks_cache_and_reasoning_are_not_added_twice(self):
        first = event('2026-10-08T01:00:00Z', 100, 20, 80, 10)
        second = event('2026-10-08T02:00:00Z', 200, 40, 160, 20)
        self.write('a.jsonl', first + first + second)
        self.write('fork.jsonl', first + second, 'archived_sessions')
        scanner = core.LocalCodexTokens(self.home)
        result = scanner.scan()
        self.assertEqual(result['today'], 240)
        self.assertEqual(result['cached'], 160)
        self.assertEqual(scanner.scan()['month'], 240)

    def test_daily_boundary_uses_beijing_and_previous_month_baseline(self):
        self.write('a.jsonl', event('2026-09-30T15:00:00Z', 100, 10) +
                   event('2026-09-30T16:10:00Z', 200, 20) +
                   event('2026-10-07T16:10:00Z', 300, 30))
        result = core.LocalCodexTokens(self.home).scan()
        self.assertEqual(result['month'], 220)
        self.assertEqual(result['today'], 110)
        self.assertEqual(result['daily']['2026-10-01'], 110)

    def test_incremental_partial_write_and_counter_reset(self):
        first = event('2026-10-08T01:00:00Z', 100, 20)
        second = event('2026-10-08T02:00:00Z', 200, 40)
        p = self.write('a.jsonl', first + second[:50])
        scanner = core.LocalCodexTokens(self.home)
        self.assertEqual(scanner.scan()['month'], 120)
        with p.open('a', encoding='utf-8') as f:
            f.write(second[50:])
        self.assertEqual(scanner.scan()['month'], 240)
        with p.open('a', encoding='utf-8') as f:
            f.write(event('2026-10-08T03:00:00Z', 50, 10))
        self.assertEqual(scanner.scan()['month'], 300)

    def test_truncation_and_archiving_rebuild_without_duplication(self):
        p = self.write('a.jsonl', event('2026-10-08T01:00:00Z', 100, 20) + event('2026-10-08T02:00:00Z', 200, 40))
        scanner = core.LocalCodexTokens(self.home)
        self.assertEqual(scanner.scan()['month'], 240)
        self.write('a.jsonl', event('2026-10-08T01:00:00Z', 50, 10))
        self.assertEqual(scanner.scan()['month'], 60)
        p.rename(self.home / 'archived_sessions' / 'a.jsonl')
        self.assertEqual(scanner.scan()['month'], 60)


class AdapterTests(unittest.TestCase):
    def test_pagination_and_loop_rejection(self):
        with patch('core.http_json', side_effect=[{'data': [1], 'has_more': True, 'next_page': 'a'}, {'data': [2], 'has_more': False}]) as req:
            self.assertEqual(core.paged('https://example.invalid', {}, {}), [1, 2])
            self.assertIn('page=a', req.call_args[0][0])
        with patch('core.http_json', return_value={'data': [1], 'has_more': True, 'next_page': 'a'}):
            with self.assertRaises(RuntimeError):
                core.paged('https://example.invalid', {}, {})

    def test_claude_includes_both_cache_creation_categories(self):
        rows = [{'starting_at': '2026-10-08T00:00:00Z', 'results': [{
            'uncached_input_tokens': 100, 'output_tokens': 20,
            'cache_read_input_tokens': 300,
            'cache_creation': {'ephemeral_1h_input_tokens': 40, 'ephemeral_5m_input_tokens': 50}}]}]
        with patch('core.get_key', return_value='fake'), patch('core.paged', return_value=rows):
            result = core.fetch_anthropic({})
        self.assertEqual(result['daily']['2026-10-08'], 510)

    def test_openai_cached_tokens_subset_and_costs_decimal(self):
        rows = [{'start_time': int(FIXED_NOW.timestamp()), 'results': [{
            'input_tokens': 100, 'output_tokens': 20, 'input_cached_tokens': 80,
            'num_model_requests': 2}]}]
        costs = [{'results': [{'amount': {'value': '1.234', 'currency': 'usd'}},
                              {'amount': {'value': '0.006', 'currency': 'usd'}}]}]
        with patch('core.get_key', return_value='fake'), patch('core.paged', side_effect=[rows, costs]):
            result = core.fetch_openai({})
        self.assertEqual(sum(result['daily'].values()), 120)
        self.assertEqual(result['metrics'][-1]['value'], '$1.24')

    def test_deepseek_balance_does_not_invent_token_history(self):
        with patch('core.get_key', return_value='fake'), patch('core.http_json', return_value={
            'is_available': True, 'balance_infos': [{'currency': 'CNY', 'total_balance': '110.00', 'granted_balance': '10.00', 'topped_up_balance': '100.00'}]}):
            result = core.fetch_deepseek({})
        self.assertEqual(result['metrics'][0]['value'], '110.00')
        self.assertFalse(any('token' in x['label'] for x in result['metrics']))

    def test_missing_key_does_not_make_network_request(self):
        with patch('core.get_key', return_value=None), patch('core.http_json') as http:
            self.assertEqual(core.fetch_deepseek({})['status'], 'unconfigured')
            http.assert_not_called()

    def test_codex_usage_null_fields_and_multiple_quota_buckets(self):
        scanner = unittest.mock.Mock()
        scanner.scan.return_value = {'today': 123, 'month': 456, 'daily': {}, 'files': 1}
        snapshot = {'primary': {'usedPercent': 99, 'windowDurationMins': 300, 'resetsAt': 1999999999}}
        rpc = {2: {'result': {'rateLimits': {'planType': 'plus'}, 'rateLimitsByLimitId': {'codex': snapshot, 'other': snapshot}}},
               3: {'result': {'summary': {'lifetimeTokens': None}, 'dailyUsageBuckets': None}}}
        with patch('core.codex_rpc', return_value=rpc):
            result = core.fetch_codex({}, scanner)
        quotas = [m for m in result['metrics'] if 'used' in m]
        self.assertEqual(len(quotas), 2)
        self.assertEqual(quotas[0]['value'], '剩余 1%')
        self.assertFalse(any(m['label'] == '账户累计 token' for m in result['metrics']))

    def test_http_error_never_exposes_sensitive_provider_message(self):
        exc = HTTPError('https://example.invalid', 401, 'SECRET-KEY', {}, None)
        self.assertNotIn('SECRET', core.safe_error(exc))


class ReminderAndKeyTests(unittest.TestCase):
    def test_expiry_is_end_of_beijing_day(self):
        stamp = core.parse_expiry('2026-11-08')
        self.assertEqual(datetime.fromtimestamp(stamp, core.TZ).hour, 23)
        self.assertEqual(datetime.fromtimestamp(stamp, core.TZ).minute, 59)

    def test_stale_data_does_not_trigger_quota_alert(self):
        r = core.card('codex', 'test', [core.metric('quota', '1%', used=99)])
        self.assertEqual(len(core.alerts([r])), 1)
        r['stale'] = True
        self.assertEqual(core.alerts([r]), [])
        r.pop('stale')
        r['metrics'][0]['reset_at'] = 1
        self.assertEqual(core.alerts([r]), [])

    def test_manual_input_validation(self):
        for value in ('nan', 'inf', '-1', '101'):
            with self.assertRaises(ValueError):
                core.validate_manual({'name': 'test', 'used_percent': value})
        core.validate_manual({'name': 'test', 'used_percent': '80', 'tokens_today': '0'})

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI only')
    def test_dpapi_round_trip(self):
        secret = 'test-key-never-used-for-network'
        encoded = core.protect(secret)
        self.assertNotIn(secret, encoded)
        self.assertEqual(core.protect(encoded, decrypt=True), secret)


if __name__ == '__main__':
    unittest.main()
