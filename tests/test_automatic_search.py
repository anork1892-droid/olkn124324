"""Discovery runs without a browser, workers or an available language model."""
import json
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from bountyos.app import create_app
from bountyos.connectors import HTTP, RateLimited
from bountyos.engine import Engine
from bountyos.store import Store
from test_api import OfflineEngine


class AutomaticSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.github = Mock()
        self.github.search.return_value = []
        self.github.http.retry_after_seconds.return_value = 0
        self.model = Mock(model='local')
        self.engine = Engine(self.store, self.github, self.model, Mock(configured=False))
        self.addCleanup(self.engine.stop)

    def test_start_discovers_without_manual_request_or_model(self):
        completed = threading.Event()
        self.github.search.side_effect = lambda: completed.set() or []
        self.assertFalse(self.store.get_settings()['running'])
        self.engine.start()
        self.assertTrue(completed.wait(3), 'No automatic search after startup')
        self.engine._scan_thread.join(2)
        self.engine.stop()
        self.assertEqual(self.github.search.call_count, 3)
        self.model.check.assert_not_called()
        self.model.generate.assert_not_called()
        self.assertFalse(self.store.get_settings()['auto_submit'])

    def test_scheduler_repeats_only_after_interval_with_work_paused(self):
        self.engine._stop = Mock()
        self.engine._stop.wait.side_effect = [False, False, False, True]
        self.engine._stop.is_set.return_value = False
        calls = []
        clock = iter((100, 130, 161))
        def scan():
            calls.append(self.now)
            self.engine._next_scan = self.now + 60
        def now():
            self.now = next(clock)
            return self.now
        with patch.object(self.engine, 'scan_now', side_effect=scan), patch('bountyos.engine.time.monotonic', side_effect=now):
            self.engine._loop()
        self.assertEqual(calls, [100, 161])
        self.model.check.assert_not_called()

    def test_explicit_search_pause_stops_scheduler(self):
        self.store.update_settings({'search_enabled': False})
        self.engine._stop = Mock()
        self.engine._stop.wait.side_effect = [False, False, True]
        with patch.object(self.engine, 'scan_now') as scan:
            self.engine._loop()
        scan.assert_not_called()
        self.assertFalse(Store(self.temp.name).get_settings()['search_enabled'])

    def test_finished_scan_respects_source_cooldown_and_records_next_time(self):
        self.github.search.side_effect = RateLimited('Wait 240 seconds')
        self.github.http.retry_after_seconds.return_value = 240
        self.engine._scan_lock.acquire()
        with patch('bountyos.engine.time.monotonic', return_value=100):
            self.engine._scan()
        self.assertEqual(self.engine._next_scan, 340)
        scout = next(agent for agent in self.store.list_agents() if agent['id'] == 'scout')
        self.assertTrue(scout['next_scan_at'])
        self.assertIn('240', scout['detail'])
        self.assertFalse(self.engine._scan_lock.locked())

    def test_http_cooldown_countdown_is_read_only(self):
        now = [100.0]
        http = HTTP(clock=lambda: now[0])
        http._backoff('120')
        self.assertEqual(http.retry_after_seconds(), 120)
        now[0] = 230
        self.assertEqual(http.retry_after_seconds(), 0)

    def test_upgrade_old_default_once_and_preserve_later_user_preferences(self):
        with self.store._connection(write=True) as conn:
            conn.execute("DELETE FROM imports WHERE name='automatic-search-v1'")
            conn.execute("DELETE FROM settings WHERE key='search_enabled'")
            conn.execute("UPDATE settings SET value='30' WHERE key='poll_interval_minutes'")
        upgraded = Store(self.temp.name)
        self.assertEqual(upgraded.get_settings()['poll_interval_minutes'], 1)
        self.assertTrue(upgraded.get_settings()['search_enabled'])
        upgraded.update_settings({'search_enabled': False, 'poll_interval_minutes': 30})
        self.assertFalse(Store(self.temp.name).get_settings()['search_enabled'])
        self.assertEqual(Store(self.temp.name).get_settings()['poll_interval_minutes'], 30)

    def test_upgrade_preserves_custom_interval(self):
        self.store.update_settings({'poll_interval_minutes': 7})
        with self.store._connection(write=True) as conn:
            conn.execute("DELETE FROM imports WHERE name='automatic-search-v1'")
        self.assertEqual(Store(self.temp.name).get_settings()['poll_interval_minutes'], 7)

    def test_search_controls_do_not_enable_work_or_submission(self):
        with TestClient(create_app(data_dir=self.temp.name, engine_factory=OfflineEngine)) as client:
            engine = client.app.state.engine
            self.assertEqual(client.put('/api/settings', json={'search_enabled': False}).status_code, 200)
            self.assertEqual(engine.scans, 0)
            self.assertEqual(client.put('/api/settings', json={'search_enabled': True, 'poll_interval_minutes': 1}).status_code, 200)
            self.assertEqual(engine.scans, 1)
            state = client.get('/api/state').json()
            self.assertFalse(state['settings']['running'])
            self.assertFalse(state['settings']['auto_submit'])
            self.assertEqual(state['settings']['daily_budget_sek'], 0)
            self.assertEqual(client.put('/api/settings', json={'search_enabled': 'yes'}).status_code, 422)
            self.assertEqual(client.put('/api/settings', json={'poll_interval_minutes': 0}).status_code, 422)
            client.put('/api/settings', json={'search_enabled': False})
            client.post('/api/control', json={'action': 'start'})
            self.assertEqual(engine.scans, 1)


if __name__ == '__main__':
    unittest.main()
