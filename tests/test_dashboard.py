import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('dashboard', Path(__file__).resolve().parents[1] / 'bin/dashboard.py')
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)

class DashboardTests(unittest.TestCase):
    def test_arabic_shaping_uses_joined_presentation_forms(self):
        self.assertEqual(dashboard.shape_arabic('فين'), 'ﻦﻴﻓ')

    def test_daily_words_have_complete_entries(self):
        words = json.loads((Path(__file__).resolve().parents[1] / 'words.json').read_text())
        for group in ('ai', 'egyptian'):
            self.assertGreaterEqual(len(words[group]), 100)
            for item in words[group]:
                self.assertTrue(all(item.get(key) for key in ('word', 'pronunciation', 'meaning')))
        self.assertTrue(all(item.get('chinese') for item in words['egyptian']))
        self.assertGreaterEqual(len(words['ai_tips']), 100)
        self.assertEqual(len(set(words['ai_tips'])), len(words['ai_tips']))
        for group in ('ai', 'egyptian'):
            self.assertEqual(len({item['word'].casefold() for item in words[group]}), len(words[group]))

    def test_last_live_quota_survives_unavailable_and_broken_input(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(dashboard, 'ROOT', directory):
            path = Path(directory) / 'agent-quotas.json'
            data = {'timestamp': int(time.time() * 1000), 'quotas': {'codex': {'status': 'live', 'groups': [{'windows': [{'id': '5h', 'remainingPercent': 0}]}]}}}
            path.write_text(json.dumps(data))
            rows, _ = dashboard.agent_table()
            self.assertEqual(rows[0][1].split()[0], '0.0%')
            path.write_text('{')
            rows, timestamp = dashboard.agent_table()
            self.assertIsNone(timestamp)
            self.assertEqual(rows[0][0], 'Codex')
            self.assertEqual(rows[0][1].split()[0], '0.0%')

    def test_api_failure_uses_last_success_and_marks_age(self):
        def success(base, token, path):
            if path == '/api/user/self':
                return {'quota': 50, 'used_quota': 10, 'request_count': 1}
            return [] if '/api/data/' in path else {}
        with tempfile.TemporaryDirectory() as directory, patch.object(dashboard, 'ROOT', directory):
            config = {'base_url': 'https://example.org', 'access_token': 'test', 'scope': 'user'}
            with patch.object(dashboard, 'fetch', success):
                dashboard.dashboard(config)
            with patch.object(dashboard, 'fetch', side_effect=OSError('offline')):
                result = dashboard.dashboard(config)
            self.assertIn('Balance: 50', result)
            self.assertIn('account offline', result)

    def test_currency_only_converted_when_server_confirms_usd(self):
        def fake_fetch(base, token, path):
            if path == '/api/user/self':
                return {'quota': 500000, 'used_quota': 0, 'request_count': 1}
            if path == '/api/status':
                return {'quota_per_unit': 500000, 'quota_display_type': 'USD'}
            return []
        with tempfile.TemporaryDirectory() as directory, patch.object(dashboard, 'ROOT', directory), patch.object(dashboard, 'fetch', fake_fetch):
            result = dashboard.dashboard({'base_url': 'https://example.org', 'access_token': 'test', 'scope': 'user'})
            self.assertIn('$1.00', result)
            self.assertIn('$0.00', result)

if __name__ == '__main__':
    unittest.main()
