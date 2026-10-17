"""Queued work survives discovery failures; all GitHub/model calls are mocked."""

import tempfile
import unittest
from unittest.mock import Mock

from bountyos.connectors import ConnectorError, RateLimited, qualify
from bountyos.engine import Engine
from bountyos.store import Store
from test_engine import issue


class ScanQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.github = Mock()
        self.github.search.return_value = []
        self.github.issue.side_effect = self.read_issue
        self.engine = Engine(
            self.store,
            github=self.github,
            model=Mock(model="test:3b"),
            submitter=Mock(configured=False),
        )

    def tearDown(self):
        self.engine.stop()
        self.temp.cleanup()

    @staticmethod
    def url(number):
        return f"https://github.com/owner/project/issues/{number}"

    def read_issue(self, url):
        return issue(url=url, number=int(url.rsplit("/", 1)[1]))

    def pending(self, number, **changes):
        candidate = qualify(self.read_issue(self.url(number)))
        candidate.update(status="discovered", reason="Väntar på ny granskning.")
        candidate.update(changes)
        task_id = self.store.upsert_task(candidate)
        return self.store.get_task(task_id)

    def scan(self):
        self.engine._scan_lock.acquire()
        self.engine._scan()
        self.assertFalse(self.engine._scan_lock.locked())

    def scout(self):
        return next(agent for agent in self.store.list_agents() if agent["id"] == "scout")

    def test_saved_task_is_assessed_before_failing_search(self):
        task = self.pending(7)
        self.github.search.side_effect = ConnectorError("Sökdata saknas")

        self.scan()

        self.assertEqual(self.store.get_task(task["id"])["status"], "qualified")
        self.assertEqual(self.github.method_calls[0].args, (task["url"],))
        self.github.search.assert_called_once()
        self.assertEqual(self.engine.connection_status()["github"]["status"], "error")
        self.assertIn("Sökdata saknas", self.scout().get("detail", ""))
        self.engine.model.generate.assert_not_called()

    def test_issue_failure_is_visible_and_does_not_stop_next_saved_task(self):
        failing = self.pending(7)
        successful = self.pending(8)

        def read(url):
            if url == failing["url"]:
                raise ConnectorError("Ärendets JSON kunde inte läsas")
            return self.read_issue(url)

        self.github.issue.side_effect = read
        self.scan()

        updated = self.store.get_task(failing["id"])
        self.assertEqual(updated["status"], "discovered")
        self.assertIn("Ärendets JSON kunde inte läsas", updated["reason"])
        self.assertEqual(self.store.get_task(successful["id"])["status"], "qualified")
        self.assertTrue(self.scout().get("detail"))

    def test_program_page_is_blocked_without_requesting_it_as_an_issue(self):
        task = self.pending(
            7,
            url="https://github.com/josedab/chronicle/blob/main/docs/BOUNTY_PROGRAM.md#-documentation-25-each",
            title="Chronicle: PromQL-exempel och FAQ",
            historical_research=True,
        )

        self.scan()

        updated = self.store.get_task(task["id"])
        self.assertEqual(updated["status"], "blocked")
        self.assertIn("github", updated["reason"].lower())
        self.assertRegex(updated["reason"].lower(), r"ärende|issue")
        self.assertNotEqual(updated["reason"], task["reason"])
        self.github.issue.assert_not_called()

    def test_rate_limit_ends_pass_and_explains_unprocessed_queue(self):
        tasks = [self.pending(number) for number in (7, 8, 9)]
        self.github.issue.side_effect = RateLimited("GitHub begränsade anropen (HTTP 429)")

        self.scan()

        self.assertEqual(self.github.issue.call_count, 1)
        self.github.search.assert_not_called()
        for task in tasks:
            with self.subTest(task=task["id"]):
                updated = self.store.get_task(task["id"])
                self.assertEqual(updated["status"], "discovered")
                self.assertTrue(updated["reason"])
                self.assertNotEqual(updated["reason"], task["reason"])
        self.assertTrue(self.scout().get("detail"))
        self.assertEqual(self.store.get_state()["metrics"]["earned_sek"], 0)

    def test_search_cannot_reactivate_existing_work_or_delivery_states(self):
        protected = ["blocked", "failed", "working", "reviewing", "ready", "submitting", "submitted", "awaiting_payment", "paid"]
        tasks = [self.pending(number, status=status, reason="Keep current outcome")
                 for number, status in enumerate(protected, start=1)]
        self.github.search.return_value = [task["url"] for task in tasks]

        self.scan()

        self.github.issue.assert_not_called()
        for task in tasks:
            with self.subTest(status=task["status"]):
                updated = self.store.get_task(task["id"])
                self.assertEqual(updated["status"], task["status"])
                self.assertEqual(updated["reason"], task["reason"])

    def test_successful_read_does_not_overwrite_task_claimed_during_request(self):
        task = self.pending(7)

        def read(url):
            self.store.update_task(task["id"], status="working", reason="Worker started")
            return self.read_issue(url)

        self.github.issue.side_effect = read
        self.github.search.return_value = [task["url"]]
        self.scan()

        updated = self.store.get_task(task["id"])
        self.assertEqual(updated["status"], "working")
        self.assertEqual(updated["reason"], "Worker started")
        self.github.issue.assert_called_once_with(task["url"])

    def test_failed_read_does_not_overwrite_task_claimed_during_request(self):
        task = self.pending(7)

        def read(url):
            self.store.update_task(task["id"], status="working", reason="Worker started")
            raise ConnectorError("Tillfälligt anslutningsfel")

        self.github.issue.side_effect = read
        self.scan()

        updated = self.store.get_task(task["id"])
        self.assertEqual(updated["status"], "working")
        self.assertEqual(updated["reason"], "Worker started")

    def test_saved_and_new_urls_share_deduplication_and_bounded_pass(self):
        tasks = [self.pending(number) for number in range(1, 13)]
        self.github.search.return_value = [tasks[0]["url"], tasks[0]["url"]] + [self.url(number) for number in range(13, 25)]

        self.scan()

        urls = [call.args[0] for call in self.github.issue.call_args_list]
        self.assertEqual(len(urls), 20)
        self.assertEqual(len(set(urls)), 20)
        self.assertTrue(all(task["url"] in urls for task in tasks))
        self.assertTrue(self.scout().get("detail"))

    def test_pending_batch_prioritizes_least_recently_checked_tasks(self):
        tasks = [self.pending(number) for number in range(1, 14)]
        self.store.update_task(tasks[0]["id"], reason="Recently checked")

        self.scan()

        urls = [call.args[0] for call in self.github.issue.call_args_list]
        self.assertEqual(len(urls), 12)
        self.assertNotIn(tasks[0]["url"], urls)
        self.assertTrue(all(task["url"] in urls for task in tasks[1:]))


if __name__ == "__main__":
    unittest.main()
