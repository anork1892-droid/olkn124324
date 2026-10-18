import tempfile
import unittest

from bountyos.store import Store


class PreparationStateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(directory.name)

    def test_claim_reserves_for_preparation_without_starting_delivery(self):
        task_id = self.store.upsert_task({
            "url": "https://github.com/example/project/issues/1",
            "title": "Documentation bounty", "status": "qualified",
        })
        claimed = self.store.claim_task("worker-1")
        self.assertEqual(claimed["status"], "preparing")
        self.assertEqual(claimed["worker_id"], "worker-1")
        self.assertEqual(claimed["attempts"], 1)
        self.assertIsNone(claimed["artifact_path"])
        self.assertIsNone(self.store.claim_task("worker-2"))
        self.assertEqual(self.store.get_task(task_id)["status"], "preparing")

    def test_preparing_agent_is_active_without_completed_work_or_income(self):
        self.store.set_agent("worker-1", status="preparing")
        self.store.set_agent("worker-2", status="idle")
        metrics = self.store.get_state()["metrics"]
        self.assertEqual(metrics["active_agents"], 1)
        self.assertEqual(metrics["completed_tasks"], 0)
        self.assertEqual(metrics["earned_sek"], 0)

    def test_checked_plan_remains_available_after_reopening_store(self):
        task_id = self.store.upsert_task({
            "url": "https://github.com/example/project/issues/1",
            "title": "Documentation bounty", "status": "qualified",
        })
        self.store.claim_task("worker-1")
        plan = {
            "summary": "Explain installation using the project README.",
            "steps": ["Read installation instructions", "Clarify the supported commands"],
            "source_paths": ["README.md"], "target_paths": ["docs/install.md"],
        }
        self.store.update_task(task_id, status="working", work_plan=plan,
                               preparation_reason="Underlaget räcker för den planerade ändringen.")
        reopened = Store(self.store.data_dir)
        task = reopened.get_task(task_id)
        self.assertEqual(task["work_plan"], plan)
        self.assertEqual(task["status"], "working")
        self.assertEqual(task["preparation_reason"], "Underlaget räcker för den planerade ändringen.")


if __name__ == "__main__":
    unittest.main()
