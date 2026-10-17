"""Exercise the actual HTTP contract without model or GitHub requests."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from bountyos.app import create_app


class OfflineEngine:
    def __init__(self, store):
        self.store = store
        self.scans = 0

    def start(self):
        pass

    def stop(self):
        pass

    def scan_now(self):
        self.scans += 1
        return True

    def connection_status(self):
        return {
            "ai": {"configured": False, "status": "offline", "detail": "Test: no local model"},
            "github": {"configured": False, "status": "public", "detail": "Test: public discovery"},
        }

    def check_connections(self):
        return self.connection_status()


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(data_dir=Path(self.temp.name), engine_factory=OfflineEngine)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_state_is_real_and_budget_is_zero(self):
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        state = response.json()
        self.assertEqual(state["settings"]["daily_budget_sek"], 0)
        self.assertEqual(state["metrics"]["earned_sek"], 0)
        self.assertEqual(state["metrics"]["active_agents"], 0)
        self.assertFalse(state["settings"]["running"])
        self.assertIn("ai", state["connections"])
        self.assertEqual(len(state["chart"]), 7)

    def test_controls_persist_without_inventing_work(self):
        response = self.client.post("/api/control", json={"action": "start"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get("/api/state").json()["settings"]["running"])
        self.assertEqual(self.client.post("/api/control", json={"action": "pause"}).status_code, 200)
        self.assertFalse(self.client.get("/api/state").json()["settings"]["running"])
        self.assertEqual(self.client.get("/api/state").json()["metrics"]["earned_sek"], 0)

    def test_settings_cannot_enable_paid_calls(self):
        self.assertGreaterEqual(self.client.put("/api/settings", json={"daily_budget_sek": 100}).status_code, 400)
        self.assertGreaterEqual(self.client.put("/api/settings", json={"max_agents": 999}).status_code, 400)
        self.assertEqual(self.client.put("/api/settings", json={"max_agents": 3}).status_code, 200)
        self.assertEqual(self.client.get("/api/state").json()["settings"]["max_agents"], 3)

    def test_foreign_websites_cannot_start_workers(self):
        response = self.client.post("/api/control", json={"action": "start"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.client.get("/api/state").json()["settings"]["running"])

    def test_untrusted_host_is_rejected_even_with_matching_origin(self):
        response = self.client.post("/api/control", json={"action": "start"}, headers={"Host": "evil.example", "Origin": "http://evil.example"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.client.get("/api/state").json()["settings"]["running"])

    def test_unrecognized_api_and_task_ids_are_not_spa_html(self):
        self.assertEqual(self.client.get("/api/not-a-real-route").status_code, 404)
        self.assertEqual(self.client.get("/api/tasks/missing/artifact").status_code, 404)
        self.assertEqual(self.client.post("/api/tasks/missing/retry", json={}).status_code, 404)

    def test_invalid_payments_do_not_become_income(self):
        response = self.client.post("/api/payments", json={"task_id": "missing", "amount_sek": 500, "reference": "not-real"})
        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/state").json()["metrics"]["earned_sek"], 0)

    def test_connection_checks_and_scan_return_real_responses(self):
        self.assertEqual(self.client.post("/api/connections/check", json={}).status_code, 200)
        self.assertEqual(self.client.post("/api/scan", json={}).status_code, 200)

    def test_explicit_retry_reassesses_completed_negative_decisions(self):
        store = self.app.state.store
        for number, status in enumerate(("skipped", "blocked", "failed"), start=200):
            with self.subTest(status=status):
                task_id = store.upsert_task({"title": "Review again", "url": f"https://github.com/example/project/issues/{number}", "status": status})
                response = self.client.post(f"/api/tasks/{task_id}/retry", json={})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(store.get_task(task_id)["status"], "discovered")
        self.assertEqual(self.app.state.engine.scans, 3)

    def test_retry_does_not_reset_active_work_or_submissions(self):
        store = self.app.state.store
        for number, status in enumerate(("working", "ready", "submitting", "submitted", "paid"), start=300):
            with self.subTest(status=status):
                task_id = store.upsert_task({"title": "Keep outcome", "url": f"https://github.com/example/project/issues/{number}", "status": status})
                response = self.client.post(f"/api/tasks/{task_id}/retry", json={})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(store.get_task(task_id)["status"], status)
        self.assertEqual(self.app.state.engine.scans, 0)

    def test_saved_engine_delivery_can_be_read_through_api(self):
        from bountyos.engine import Engine

        store = self.app.state.store
        task_id = store.upsert_task({"title": "Improve documentation", "url": "https://github.com/example/project/issues/123", "status": "reviewing"})
        task = store.get_task(task_id)
        context = {"repo": "example/project", "branch": "main", "files": {"README.md": "Original text"}}
        delivery = {"files": [{"path": "README.md", "content": "Improved documentation."}], "summary": "Clarifies the documentation."}
        path = Engine(store)._save_artifact(task, context, delivery, {"accepted": True, "findings": []})
        store.update_task(task_id, status="ready", artifact_path=str(path))
        response = self.client.get(f"/api/tasks/{task_id}/artifact")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Improved documentation.", response.text)

    def test_artifact_cannot_read_outside_data_directory(self):
        store = self.app.state.store
        task_id = store.upsert_task({"title": "Invalid artifact", "url": "https://github.com/example/project/issues/124", "artifact_path": "/etc/passwd"})
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}/artifact").status_code, 404)


if __name__ == "__main__":
    unittest.main()
