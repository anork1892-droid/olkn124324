"""Discovery progresses past duplicates and preserves unprocessed results."""
import tempfile
import unittest
from unittest.mock import Mock, patch

from bountyos.connectors import RateLimited, qualify
from bountyos.engine import Engine
from bountyos.store import Store
from test_engine import issue


class DiscoveryQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.github = Mock()
        self.github.http.retry_after_seconds.return_value = 0
        self.github.search.return_value = []
        self.github.issue.side_effect = lambda url: issue(url=url)
        self.engine = Engine(self.store, self.github, Mock(model="test:3b"), Mock(configured=False))
        self.addCleanup(self.engine.stop)

    @staticmethod
    def url(number):
        return f'https://github.com/owner/project/issues/{number}'

    def add(self, number, status='blocked', **changes):
        return self.store.upsert_task({**qualify(issue(url=self.url(number))), 'status': status, **changes})

    def scan(self):
        self.engine._scan_lock.acquire()
        self.engine._scan()
        self.assertFalse(self.engine._scan_lock.locked())

    def test_known_page_then_empty_page_then_unseen_task_can_be_claimed(self):
        self.add(1)
        self.github.search.side_effect = [[self.url(1)], [], [self.url(2)]]
        self.scan()
        self.assertEqual(self.github.search.call_count, 3)
        self.github.issue.assert_called_once_with(self.url(2))
        self.assertEqual(self.store.claim_task('worker-1')['url'], self.url(2))
        self.engine.model.generate.assert_not_called()

    def test_duplicate_only_pass_is_bounded_and_does_not_recheck_qualified(self):
        self.add(1, 'qualified')
        self.github.search.return_value = [self.url(1)]
        self.scan()
        self.assertEqual(self.github.search.call_count, 3)
        self.github.issue.assert_not_called()

    def test_stop_searching_after_new_results(self):
        self.github.search.return_value = [self.url(1), self.url(2)]
        self.scan()
        self.github.search.assert_called_once()
        self.assertEqual(self.github.issue.call_count, 2)

    def test_cooldown_aborts_bounded_search_immediately(self):
        self.add(1)
        self.github.search.side_effect = [[self.url(1)], RateLimited('HTTP 429'), [self.url(2)]]
        self.scan()
        self.assertEqual(self.github.search.call_count, 2)
        self.github.issue.assert_not_called()
        self.assertEqual(self.engine.connection_status()['github']['status'], 'error')

    def test_new_results_survive_twenty_read_cap_and_restart(self):
        for n in range(1, 13):
            self.add(n, 'discovered')
        self.github.search.return_value = [self.url(n) for n in range(13, 25)]
        self.scan()
        self.assertEqual(self.github.issue.call_count, 20)
        self.assertEqual(len(self.store.list_tasks()), 24)
        reopened = Store(self.temp.name)
        self.assertEqual(sum(t['status'] == 'discovered' for t in reopened.list_tasks()), 4)
        self.github.issue.reset_mock()
        self.github.search.return_value = []
        self.engine = Engine(reopened, self.github, Mock(model="test:3b"), Mock(configured=False))
        self.scan()
        self.assertEqual(self.github.issue.call_count, 4)
        self.assertFalse(any(t['status'] == 'discovered' for t in reopened.list_tasks()))

    def test_failed_new_issue_read_stays_queued(self):
        self.github.search.return_value = [self.url(1), self.url(2)]
        self.github.issue.side_effect = RateLimited('HTTP 429')
        self.scan()
        self.assertEqual(len(self.store.list_tasks()), 2)
        self.assertTrue(all(t['status'] == 'discovered' for t in self.store.list_tasks()))
        self.github.issue.side_effect = lambda url: issue(url=url)
        self.github.issue.reset_mock()
        self.github.search.return_value = []
        self.scan()
        self.assertEqual(self.github.issue.call_count, 2)

    def test_known_tasks_do_not_consume_issue_read_budget(self):
        for n in range(1, 13):
            self.add(n, 'discovered')
        for n in range(13, 24):
            self.add(n, 'blocked')
        self.github.search.return_value = [self.url(n) for n in range(13, 25)]
        self.scan()
        self.assertEqual(self.github.issue.call_count, 13)
        self.assertEqual(self.store.claim_task('worker-1')['status'], 'working')
        self.assertIn(self.url(24), [c.args[0] for c in self.github.issue.call_args_list])

    def test_no_work_summary_explains_saved_blockers(self):
        self.add(1, reason='Uppdraget är inte öppet.')
        self.add(2, 'skipped', reason='Ingen uttrycklig ersättning kunde verifieras.')
        self.scan()
        scout = next(a for a in self.store.list_agents() if a['id'] == 'scout')
        self.assertIn('Inga uppdrag är redo att påbörjas', scout['detail'])
        self.assertIn('saknad eller föreslagen ersättning', scout['detail'])
        self.assertIn('stängda, upptagna', scout['detail'])
        self.assertIsNone(self.store.claim_task('worker-1'))

    def test_old_scope_blocks_rechecked_once_without_restarting_deliveries(self):
        reason = 'Uppdraget kräver kod, mätningar eller körning utanför dokumentationsagentens stöd.'
        task_id = self.add(1, reason=reason)
        protected_id = self.add(2, reason=reason, artifact_path='/saved/delivery.md')
        with patch.object(self.engine, '_loop'):
            self.engine.start()
            self.engine._scheduler.join(1)
        self.assertEqual(self.store.get_task(task_id)['status'], 'discovered')
        self.assertEqual(self.store.get_task(protected_id)['status'], 'blocked')
        self.store.update_task(task_id, status='blocked', reason=reason)
        with patch.object(self.engine, '_loop'):
            self.engine.start()
            self.engine._scheduler.join(1)
        self.assertEqual(self.store.get_task(task_id)['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
