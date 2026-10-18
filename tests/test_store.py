from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from bountyos.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def task(self, number=1, **fields):
        return self.store.upsert_task({
            "url": f"https://github.com/example/project/issues/{number}",
            "title": f"Real candidate {number}", "repo": "example/project",
            "reward_amount": 250, "reward_currency": "USD", **fields,
        })

    def test_persistence_and_dedup_preserve_work_in_progress(self):
        task_id = self.task(status="qualified", source_facts={"verified": True})
        claimed = self.store.claim_task("worker-1")
        self.assertEqual(claimed["id"], task_id)
        repeated_id = self.task(status="blocked", reason="source refreshed", title="Updated title")
        self.assertEqual(task_id, repeated_id)
        reopened = Store(self.tmp.name)
        self.assertEqual(len(reopened.list_tasks()), 1)
        task = reopened.get_task(task_id)
        self.assertEqual(task["status"], "working")
        self.assertEqual(task["worker_id"], "worker-1")
        self.assertEqual(task["source_facts"], {"verified": True})
        self.assertEqual(task["title"], "Updated title")
        self.assertEqual(task["attempts"], 1)

    def test_atomic_claim_never_gives_a_task_to_two_workers(self):
        ids = {self.task(number, status="qualified") for number in range(8)}
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda worker: self.store.claim_task(str(worker)), range(20)))
        claimed = [task["id"] for task in results if task]
        self.assertEqual(set(claimed), ids)
        self.assertEqual(len(claimed), len(ids))
        self.assertTrue(all(task["status"] == "working" for task in self.store.list_tasks()))

    def test_advertised_reward_and_submission_never_count_as_earnings(self):
        task_id = self.task(status="submitted")
        metrics = self.store.get_state()["metrics"]
        self.assertEqual(metrics["earned_sek"], 0)
        self.assertEqual(metrics["pending_sek"], 0)  # USD has no invented conversion.
        self.assertEqual(metrics["net_sek"], 0)
        self.store.update_task(task_id, status="awaiting_payment")
        self.assertEqual(self.store.get_state()["metrics"]["earned_sek"], 0)
        self.store.record_payment(task_id, 102.45, "bank-tx-123")
        state = Store(self.tmp.name).get_state()
        self.assertEqual(state["metrics"]["earned_sek"], 102.45)
        self.assertEqual(state["metrics"]["net_sek"], 102.45)
        self.assertEqual(state["chart"][-1]["earned"], 102.45)
        self.assertEqual(self.store.get_task(task_id)["payment_verification"], "manual")

    def test_duplicate_payment_reference_does_not_change_either_task(self):
        first = self.task(1, status="submitted")
        second = self.task(2, status="submitted")
        self.store.record_payment(first, 10, "reference")
        with self.assertRaisesRegex(ValueError, "redan registrerad"):
            self.store.record_payment(second, 50, "reference")
        self.assertEqual(self.store.get_task(second)["status"], "submitted")
        self.assertEqual(self.store.get_state()["metrics"]["earned_sek"], 10)

    def test_payment_requires_submission_and_positive_exact_money(self):
        task_id = self.task(status="blocked")
        with self.assertRaises(ValueError):
            self.store.record_payment(task_id, 10, "before-submission")
        self.store.update_task(task_id, status="submitted")
        for amount in (0, -1, float("nan"), float("inf"), 1.001, True):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.store.record_payment(task_id, amount, "invalid-amount")
        self.assertEqual(self.store.get_state()["metrics"]["earned_sek"], 0)

    def test_budget_is_fixed_and_failed_updates_are_atomic(self):
        for value in (1, -1, "0", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.update_settings({"max_agents": 8, "daily_budget_sek": value})
        settings = self.store.get_settings()
        self.assertEqual(settings["max_agents"], 2)
        self.assertEqual(settings["daily_budget_sek"], 0)
        self.store.update_settings({"daily_budget_sek": 0, "max_agents": 3})
        self.assertEqual(Store(self.tmp.name).get_settings()["max_agents"], 3)
        for changes in ({"max_agents": 11}, {"max_agents": True}, {"poll_interval_minutes": 0}, {"auto_submit": "true"}, {"paid_provider": "remote"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.update_settings(changes)

    def test_import_is_once_and_preserves_seven_candidates_in_six_sources(self):
        source = Path(__file__).resolve().parents[1] / "pilot" / "research.json"
        self.assertEqual(self.store.import_research(source), 6)
        self.assertEqual(Store(self.tmp.name).import_research(source), 0)
        tasks = self.store.list_tasks()
        self.assertEqual(len(tasks), 6)
        self.assertTrue(all(task["status"] == "blocked" for task in tasks))
        self.assertEqual(sum(len(task.get("historical_candidates", [task["original_research"]])) for task in tasks), 7)
        by_repo = {task["repo"]: task for task in tasks}
        self.assertEqual(by_repo["oll4com/spaceapp"]["reward_currency"], "USD")
        self.assertEqual(by_repo["aLexzzz430/Cognitive-OS"]["reward_amount"], 3000)
        self.assertNotEqual(by_repo["sybil-solutions/local-ai-registry"]["reward_currency"], "USD")
        self.assertIsNone(by_repo["josedab/chronicle"]["reward_amount"])
        self.assertEqual(self.store.get_state()["metrics"]["earned_sek"], 0)


if __name__ == "__main__":
    unittest.main()
