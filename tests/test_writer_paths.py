"""Writer path corrections remain bounded and pass the normal delivery gates."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bountyos.connectors import Ollama, delivery_schema, qualify
from bountyos.engine import Engine, PREPARE_SYSTEM, REVIEW_SYSTEM, WRITER_SYSTEM
from bountyos.store import Store
from test_engine import delivery, issue, work_plan


PLACEHOLDER_REASON = (
    "Modellen föreslog filer utanför det hämtade underlaget: docs/name.md. "
    "Denna version kan bara ändra de inlästa dokumenten."
)


def unread_delivery(path="docs/name.md"):
    value = delivery()
    value["files"][0]["path"] = path
    value["files"][0]["content"] = "# Generated draft that is not repository evidence\n"
    return value


class WriterPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.update_settings({"running": True, "auto_submit": False})
        self.context = {"repo": "owner/project", "branch": "main", "files": {
            "docs/existing.md": "# Existing guide\n",
            "README.md": "# Example\n",
        }}
        self.github = Mock()
        self.github.issue.return_value = issue()
        self.github.repository_context.return_value = self.context
        self.model = Mock()
        self.submitter = Mock(configured=False)
        self.engine = Engine(self.store, self.github, self.model, self.submitter)
        self.addCleanup(self.engine.stop)

    def run_work(self, responses):
        task_id = self.store.upsert_task(qualify(issue()))
        task = self.store.claim_task("worker-1")
        plan = work_plan(paths=sorted(self.context["files"]))
        if callable(responses):
            self.model.generate.side_effect = lambda system, data, **kwargs: plan if system == PREPARE_SYSTEM else responses(system, data, **kwargs)
        else:
            self.model.generate.side_effect = [plan, *responses]
        self.engine._work("worker-1", task)
        return self.store.get_task(task_id)

    def assert_no_delivery(self, task):
        self.assertFalse(task.get("artifact_path"))
        self.submitter.submit.assert_not_called()
        self.assertFalse((Path(self.temp.name) / "artifacts").exists())
        self.assertEqual(self.store.get_state()["metrics"]["earned_sek"], 0)

    def test_writer_receives_only_actual_paths_in_prompt_and_schema(self):
        task = self.run_work([delivery(), {"accepted": True, "findings": []}])

        self.assertEqual(task["status"], "ready")
        writer = self.model.generate.call_args_list[1]
        self.assertEqual(writer.args[0], WRITER_SYSTEM)
        self.assertNotIn("docs/name.md", writer.args[0])
        self.assertEqual(writer.args[1]["editable_paths"], ["README.md", "docs/existing.md"])
        allowed = writer.kwargs["schema"]["properties"]["files"]["items"]["properties"]["path"]["enum"]
        self.assertEqual(set(allowed), set(self.context["files"]))
        self.assertEqual(self.model.generate.call_count, 3)
        self.assertEqual(self.model.generate.call_args_list[0].args[0], PREPARE_SYSTEM)
        self.assertEqual(self.model.generate.call_args_list[2].args[0], REVIEW_SYSTEM)
        self.submitter.submit.assert_not_called()

    def test_unread_path_is_regenerated_then_independently_reviewed(self):
        wrong = unread_delivery()
        task = self.run_work([wrong, delivery(), {"accepted": True, "findings": []}])

        self.assertEqual(task["status"], "ready")
        self.assertEqual(self.model.generate.call_count, 4)
        initial, corrected, reviewed = self.model.generate.call_args_list[1:]
        self.assertEqual(initial.args[0], WRITER_SYSTEM)
        self.assertEqual(corrected.args[0], WRITER_SYSTEM)
        self.assertEqual(reviewed.args[0], REVIEW_SYSTEM)
        self.assertEqual(corrected.args[1]["repository"], self.context)
        self.assertEqual(corrected.args[1]["path_correction"]["invalid_paths"], ["docs/name.md"])
        self.assertNotIn(wrong["files"][0]["content"].strip(), json.dumps(corrected.args[1]))
        self.assertEqual(corrected.kwargs["schema"], initial.kwargs["schema"])
        self.assertEqual(reviewed.args[1]["proposed_delivery"], delivery())
        artifact = Path(task["artifact_path"])
        self.assertEqual((artifact.parent / "files/README.md").read_text(), delivery()["files"][0]["content"])
        self.assertFalse((artifact.parent / "files/docs/name.md").exists())
        self.submitter.submit.assert_not_called()
        self.assertEqual(self.store.get_state()["metrics"]["earned_sek"], 0)

    def test_second_unread_path_blocks_without_a_third_writer_or_review(self):
        task = self.run_work([unread_delivery(), unread_delivery("docs/another.md")])

        self.assertEqual(task["status"], "blocked")
        self.assertIn("docs/another.md", task["reason"])
        self.assertEqual(self.model.generate.call_count, 3)
        self.assert_no_delivery(task)

    def test_empty_correction_reports_unsupported_work_without_creating_a_file(self):
        task = self.run_work([unread_delivery(), {"files": [], "summary": "The issue requires a new file."}])

        self.assertEqual(task["status"], "blocked")
        self.assertIn("requires a new file", task["reason"])
        self.assertEqual(self.model.generate.call_count, 3)
        self.assert_no_delivery(task)

    def test_initial_empty_result_is_not_retried(self):
        task = self.run_work([{"files": [], "summary": "Insufficient evidence."}])

        self.assertEqual(task["status"], "blocked")
        self.assertIn("Insufficient evidence", task["reason"])
        self.assertEqual(self.model.generate.call_count, 2)
        self.assert_no_delivery(task)

    def test_unsafe_path_is_not_sent_for_corrective_generation(self):
        task = self.run_work([unread_delivery("docs/../../README.md")])

        self.assertEqual(task["status"], "blocked")
        self.assertIn("otillåten sökväg", task["reason"])
        self.assertEqual(self.model.generate.call_count, 2)
        self.assert_no_delivery(task)

    def test_oversized_unread_file_is_not_sent_for_corrective_generation(self):
        wrong = unread_delivery()
        wrong["files"][0]["content"] = "x" * 80_001
        task = self.run_work([wrong])

        self.assertEqual(task["status"], "blocked")
        self.assertIn("för stor", task["reason"])
        self.assertEqual(self.model.generate.call_count, 2)
        self.assert_no_delivery(task)

    def test_corrected_path_still_cannot_claim_unperformed_tests(self):
        correction = delivery()
        correction["summary"] = "All tests passed."
        task = self.run_work([unread_delivery(), correction])

        self.assertEqual(task["status"], "blocked")
        self.assertIn("påstår att tester", task["reason"])
        self.assertEqual(self.model.generate.call_count, 3)
        self.assert_no_delivery(task)

    def test_corrected_path_still_requires_reviewer_acceptance(self):
        rejected = {"accepted": False, "findings": ["The requested evidence is missing."]}
        task = self.run_work([unread_delivery(), delivery(), rejected, delivery(), rejected])

        self.assertEqual(task["status"], "blocked")
        self.assertIn("requested evidence", task["reason"])
        self.assertTrue(Path(task["artifact_path"]).is_file())
        self.assertEqual(self.model.generate.call_count, 6)
        self.assertEqual([call.args[0] for call in self.model.generate.call_args_list],
                         [PREPARE_SYSTEM, WRITER_SYSTEM, WRITER_SYSTEM, REVIEW_SYSTEM, WRITER_SYSTEM, REVIEW_SYSTEM])
        self.submitter.submit.assert_not_called()

    def test_pause_during_generation_prevents_retry(self):
        def pause(*args, **kwargs):
            self.store.update_settings({"running": False})
            return unread_delivery()

        task = self.run_work(pause)

        self.assertEqual(task["status"], "qualified")
        self.assertEqual(self.model.generate.call_count, 2)
        self.assert_no_delivery(task)

    def test_stop_during_generation_prevents_retry(self):
        def stop(*args, **kwargs):
            self.engine._stop.set()
            return unread_delivery()

        task = self.run_work(stop)

        self.assertEqual(task["status"], "qualified")
        self.assertEqual(self.model.generate.call_count, 2)
        self.assert_no_delivery(task)

    def test_pause_during_successful_correction_prevents_review(self):
        replies = iter([unread_delivery(), delivery()])

        def pause_on_correction(*args, **kwargs):
            result = next(replies)
            if result["files"][0]["path"] == "README.md":
                self.store.update_settings({"running": False})
            return result

        task = self.run_work(pause_on_correction)

        self.assertEqual(task["status"], "qualified")
        self.assertEqual(self.model.generate.call_count, 3)
        self.assert_no_delivery(task)


class WriterPathMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.update_settings({"running": False, "search_enabled": False})
        self.github = Mock()
        self.model = Mock()
        self.submitter = Mock(configured=False)
        self.engine = Engine(self.store, self.github, self.model, self.submitter)
        self.addCleanup(self.engine.stop)

    def task(self, number=7, **changes):
        value = qualify(issue(url=f"https://github.com/owner/project/issues/{number}", number=number))
        value.update(status="blocked", reason=PLACEHOLDER_REASON, attempts=1, worker_id="worker-1")
        value.update(changes)
        return self.store.upsert_task(value)

    def start_without_scheduler(self):
        with patch.object(self.engine, "_loop"):
            self.engine.start()
            self.engine._scheduler.join(timeout=2)

    def test_exact_old_placeholder_failure_is_requeued_only_once(self):
        task_id = self.task()
        before = self.store.get_task(task_id)

        self.start_without_scheduler()

        after = self.store.get_task(task_id)
        self.assertEqual(after["status"], "discovered")
        self.assertTrue(after["writer_path_recheck_v1"])
        for field in ("attempts", "worker_id", "url", "reward_amount", "reward_currency"):
            self.assertEqual(after[field], before[field])
        self.store.update_task(task_id, status="blocked", reason=PLACEHOLDER_REASON)
        protected = self.store.get_task(task_id)
        self.start_without_scheduler()
        self.assertEqual(self.store.get_task(task_id), protected)
        self.github.issue.assert_not_called()
        self.model.generate.assert_not_called()
        self.submitter.submit.assert_not_called()

    def test_other_failures_and_existing_deliveries_are_not_requeued(self):
        cases = [
            {"artifact_path": "/saved/delivery.md"},
            {"submission_url": "https://github.com/owner/project/pull/9"},
            {"writer_path_recheck_v1": True},
            {"reason": PLACEHOLDER_REASON.replace("docs/name.md", "docs/real-new-file.md"), "new_document_recheck_v1": True},
            {"reason": "Granskningen godkände inte leveransen."},
            *({"status": status} for status in ("failed", "ready", "submitted", "awaiting_payment", "paid")),
        ]
        before = [self.store.get_task(self.task(number, **change))
                  for number, change in enumerate(cases, start=10)]

        self.start_without_scheduler()

        self.assertEqual([self.store.get_task(task["id"]) for task in before], before)
        self.assertFalse(self.github.method_calls)
        self.model.generate.assert_not_called()
        self.submitter.submit.assert_not_called()

    def test_migration_still_requires_current_issue_eligibility(self):
        task_id = self.task()
        self.start_without_scheduler()
        self.github.issue.return_value = issue(state="CLOSED")
        self.github.search.return_value = []
        self.engine._scan_lock.acquire()

        self.engine._scan()

        after = self.store.get_task(task_id)
        self.assertEqual(after["status"], "blocked")
        self.assertIn("inte öppet", after["reason"])
        self.github.issue.assert_called_once_with(after["url"])
        self.model.generate.assert_not_called()
        self.submitter.submit.assert_not_called()


class OllamaWriterSchemaTests(unittest.TestCase):
    def test_ollama_sends_writer_schema_and_preserves_plain_json_for_review(self):
        for output_schema in (delivery_schema(["README.md", "docs/existing.md"]), None):
            with self.subTest(schema=output_schema is not None):
                transport = Mock()
                transport.json.side_effect = [
                    {"models": [{"name": "test:7b"}]},
                    {"model_info": {"general.architecture": "test"}},
                    {"response": json.dumps(delivery())},
                ]
                model = Ollama(http=transport, model="test:7b", url="http://127.0.0.1:11434")

                result = model.generate("system", {"editable_paths": ["README.md"]}, schema=output_schema)

                self.assertEqual(result, delivery())
                self.assertEqual(transport.json.call_count, 3)
                request = transport.json.call_args
                self.assertEqual(request.args[0], "http://127.0.0.1:11434/api/generate")
                self.assertEqual(request.kwargs["payload"]["format"], output_schema or "json")
                self.assertFalse(request.kwargs["payload"]["stream"])


if __name__ == "__main__":
    unittest.main()
