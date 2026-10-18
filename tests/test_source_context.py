"""Original bounty sources and document failures must remain distinct."""
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from bountyos.connectors import ConnectorError, GitHub, HTTPFailure, RateLimited, qualify
from bountyos.engine import Engine
from bountyos.store import Store
from test_engine import issue


class OriginalSourceTests(unittest.TestCase):
    mirror = 'https://github.com/list/catalog/issues/1'
    original = 'https://github.com/owner/project/issues/7'

    def repost(self, original=None):
        return issue(url=self.mirror, repo='list/catalog', number=1,
                     body='### 原始链接 / Source URL\n' + (original or self.original) + '\n\n### Description\nPays $60 for documentation.')

    def test_repost_cannot_qualify_using_catalog_owners_authority(self):
        result = qualify(self.repost())
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['original_issue_url'], self.original)

    def test_other_reference_links_are_not_source_redirects(self):
        result = qualify(issue(body='We pay 25 USD. See also ' + self.mirror))
        self.assertEqual(result['status'], 'qualified')
        self.assertNotIn('original_issue_url', result)

    def test_invalid_ambiguous_and_self_references_block_without_following(self):
        for url in ('http://127.0.0.1:11434', 'https://evil.example/issues/1', self.mirror,
                    self.original + '\nhttps://github.com/owner/project/issues/8'):
            with self.subTest(url=url):
                result = qualify(self.repost(url))
                self.assertEqual(result['status'], 'blocked')
                self.assertNotIn('original_issue_url', result)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.github = Mock()
        self.github.search.return_value = [self.mirror]
        self.engine = Engine(self.store, self.github, Mock(), Mock(configured=False))
        self.addCleanup(self.engine.stop)

    def scan(self):
        self.engine._scan_lock.acquire()
        self.engine._scan()

    def test_closed_original_is_not_work_on_an_open_mirror(self):
        fixtures = {self.mirror: self.repost(), self.original: issue(state='CLOSED')}
        self.github.issue.side_effect = fixtures.__getitem__
        self.scan()
        by_url = {task['url']: task for task in self.store.list_tasks()}
        self.assertEqual(by_url[self.mirror]['status'], 'skipped')
        self.assertEqual(by_url[self.original]['status'], 'blocked')
        self.assertIn('inte öppet', by_url[self.original]['reason'])
        self.assertIsNone(self.store.claim_task('worker-1'))
        self.engine.model.generate.assert_not_called()

    def test_original_supplies_its_own_reward_and_authority(self):
        fixtures = {self.mirror: self.repost(), self.original: issue(title='Docs bounty 10 USD', body='Pays 10 USD for README changes.')}
        self.github.issue.side_effect = fixtures.__getitem__
        self.scan()
        task = self.store.claim_task('worker-1')
        self.assertEqual(task['url'], self.original)
        self.assertEqual(task['reward_amount'], 10)
        self.assertEqual(self.store.get_state()['metrics']['earned_sek'], 0)

    def test_original_read_failure_stays_queued_for_next_scan(self):
        def read(url):
            if url == self.mirror:
                return self.repost()
            raise RateLimited('HTTP 429')
        self.github.issue.side_effect = read
        self.scan()
        original = next(t for t in self.store.list_tasks() if t['url'] == self.original)
        self.assertEqual(original['status'], 'discovered')
        self.assertIn('429', original['reason'])
        self.github.issue.reset_mock(side_effect=True)
        self.github.issue.return_value = issue(state='CLOSED')
        self.scan()
        self.github.issue.assert_called_once_with(self.original)
        self.assertEqual(self.store.get_task(original['id'])['status'], 'blocked')

    def test_worker_recheck_queues_original_without_reading_catalog_docs(self):
        self.store.update_settings({'running': True})
        task_id = self.store.upsert_task({'url': self.mirror, 'status': 'qualified'})
        task = self.store.claim_task('worker-1')
        self.github.issue.return_value = self.repost()
        self.engine._work('worker-1', task)
        self.assertEqual(self.store.get_task(task_id)['status'], 'skipped')
        self.assertTrue(any(t['url'] == self.original and t['status'] == 'discovered' for t in self.store.list_tasks()))
        self.github.repository_context.assert_not_called()
        self.engine.model.generate.assert_not_called()

    def test_existing_original_delivery_is_never_requeued(self):
        task_id = self.store.upsert_task({'url': self.original, 'status': 'submitted', 'reason': 'Keep delivery'})
        before = self.store.get_task(task_id)
        self.github.issue.return_value = self.repost()
        self.scan()
        self.github.issue.assert_called_once_with(self.mirror)
        self.assertEqual(self.store.get_task(task_id), before)

    def test_source_cycles_stop_without_reopening_completed_decisions(self):
        fixtures = {self.mirror: self.repost(), self.original: issue(body='### Source URL\n' + self.mirror)}
        self.github.issue.side_effect = fixtures.__getitem__
        self.scan()
        self.assertEqual(self.github.issue.call_count, 2)
        self.assertEqual(len(self.store.list_tasks()), 2)
        self.assertTrue(all(t['status'] == 'skipped' for t in self.store.list_tasks()))

    def test_source_chain_shares_existing_twenty_read_budget(self):
        self.github.search.return_value = ['https://github.com/owner/project/issues/1']
        def read(url):
            n = int(url.rsplit('/', 1)[1])
            return issue(url=url, number=n, body=f'### Source URL\nhttps://github.com/owner/project/issues/{n+1}')
        self.github.issue.side_effect = read
        self.scan()
        self.assertEqual(self.github.issue.call_count, 20)
        self.assertEqual(sum(t['status'] == 'discovered' for t in self.store.list_tasks()), 1)

    def test_old_document_failure_requeues_once_but_deliveries_are_protected(self):
        reason = 'Inga befintliga dokument kunde hämtas som underlag.'
        first = self.store.upsert_task({'url': self.mirror, 'status': 'blocked', 'reason': reason, 'attempts': 1})
        protected = self.store.upsert_task({'url': self.original, 'status': 'blocked', 'reason': reason, 'artifact_path': '/saved/delivery.md'})
        with patch.object(self.engine, '_loop'):
            self.engine.start()
            self.engine._scheduler.join(2)
            self.assertEqual(self.store.get_task(first)['status'], 'discovered')
            self.assertEqual(self.store.get_task(protected)['status'], 'blocked')
            self.store.update_task(first, status='blocked', reason=reason)
            self.engine.start()
            self.engine._scheduler.join(2)
            self.assertEqual(self.store.get_task(first)['status'], 'blocked')
        self.github.issue.assert_not_called()


class DocumentFetchTests(unittest.TestCase):
    def context(self, responses):
        transport = Mock()
        transport.get.side_effect = responses
        client = GitHub(http=transport)
        return client, transport

    def test_only_not_found_is_skipped_and_existing_readme_survives(self):
        client, transport = self.context([json.dumps({'defaultBranch': 'main'}), HTTPFailure(404), '# Real README', HTTPFailure(404)])
        result = client.repository_context(issue())
        self.assertEqual(result['files'], {'README.md': '# Real README'})
        self.assertEqual(transport.get.call_count, 4)

    def test_file_network_failures_are_reported_and_not_swallowed(self):
        for failure in (ConnectorError('certificate failure'), HTTPFailure(403), RateLimited('HTTP 429')):
            with self.subTest(failure=failure):
                client, transport = self.context([json.dumps({'defaultBranch': 'main'}), HTTPFailure(404), failure])
                with self.assertRaisesRegex(ConnectorError, 'README.md.*' + str(failure)):
                    client.repository_context(issue())
                self.assertEqual(transport.get.call_count, 3)

    def test_directory_failure_does_not_trigger_more_file_requests(self):
        client, transport = self.context([json.dumps({'defaultBranch': 'main'}), HTTPFailure(403)])
        with self.assertRaisesRegex(ConnectorError, 'Dokumentlistan.*403'):
            client.repository_context(issue())
        self.assertEqual(transport.get.call_count, 2)

    def test_missing_docs_report_checked_paths(self):
        client, _ = self.context([json.dumps({'defaultBranch': 'main'}), HTTPFailure(404), HTTPFailure(404), HTTPFailure(404)])
        with self.assertRaisesRegex(ConnectorError, 'README.md, FAQ.md'):
            client.repository_context(issue())


if __name__ == '__main__':
    unittest.main()
