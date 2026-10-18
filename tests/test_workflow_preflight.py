"""Preparation and bounded revision use local model fixtures, never external writes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bountyos.connectors import ConnectorError, qualify
from bountyos.engine import Engine, PREPARE_SYSTEM, REVIEW_SYSTEM, WRITER_SYSTEM
from bountyos.store import Store
from test_engine import delivery, issue, work_plan


class WorkflowPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.update_settings({"running": True, "search_enabled": False, "auto_submit": False})
        self.context = {"repo": "owner/project", "branch": "main", "commit": "a" * 40,
                        "files": {"README.md": "# Example\n\nThe documented option selects the example.\n"},
                        "new_files": []}
        self.github = Mock()
        self.github.issue.return_value = issue()
        self.github.repository_context.return_value = self.context
        self.model = Mock()
        self.submitter = Mock(configured=False)
        self.engine = Engine(self.store, self.github, self.model, self.submitter)
        self.addCleanup(self.engine.stop)
        self.task_id = self.store.upsert_task(qualify(issue()))

    def tearDown(self):
        self.assertEqual(self.store.get_settings()["daily_budget_sek"], 0)
        metrics = self.store.get_state()["metrics"]
        self.assertEqual((metrics["earned_sek"], metrics["estimated_cost_sek"]), (0, 0))
        self.submitter.submit.assert_not_called()

    def run_work(self, responses):
        self.model.generate.side_effect = responses
        task = self.store.claim_task("worker-1")
        self.engine._work("worker-1", task)
        return self.store.get_task(self.task_id)

    def assert_no_artifact(self, task):
        self.assertFalse(task.get("artifact_path"))
        self.assertFalse((Path(self.temp.name) / "artifacts").exists())

    def test_prepare_work_review_are_distinct_and_plan_bounds_writer(self):
        self.context["files"]["docs/existing.md"] = "# Existing reference\n"
        observed = []

        def generate(system, data, **kwargs):
            task = self.store.get_task(self.task_id)
            agents = {agent["id"]: agent for agent in self.store.list_agents()}
            observed.append((system, task["status"]))
            if system == PREPARE_SYSTEM:
                self.assertEqual(agents["worker-1"]["status"], "preparing")
                self.assertEqual(data["repository"], self.context)
                self.assertIsInstance(kwargs.get("schema"), dict)
                return work_plan()
            if system == WRITER_SYSTEM:
                self.assertEqual(agents["worker-1"]["status"], "working")
                expected_plan = {key: value for key, value in work_plan().items() if key != "feasible"}
                self.assertEqual(data["work_plan"], expected_plan)
                self.assertEqual(task["work_plan"], expected_plan)
                self.assertEqual(data["editable_paths"], ["README.md"])
                enum = kwargs["schema"]["properties"]["files"]["items"]["properties"]["path"]["enum"]
                self.assertEqual(enum, ["README.md"])
                return delivery()
            self.assertEqual(system, REVIEW_SYSTEM)
            self.assertEqual(agents["review-worker-1"]["status"], "reviewing")
            return {"accepted": True, "findings": []}

        task = self.run_work(generate)

        self.assertEqual(observed, [(PREPARE_SYSTEM, "preparing"), (WRITER_SYSTEM, "working"), (REVIEW_SYSTEM, "reviewing")])
        self.assertEqual(task["status"], "ready")
        envelope = json.loads((Path(task["artifact_path"]).parent / "delivery.json").read_text())
        self.assertEqual(envelope["delivery"], delivery())

    def test_missing_documents_blocks_before_any_model_call(self):
        self.github.repository_context.side_effect = ConnectorError("Inga stödda dokument hittades i målprojektet.")
        task = self.run_work([])
        self.assertEqual(task["status"], "blocked")
        self.assertIn("Inga stödda dokument", task["reason"])
        self.model.generate.assert_not_called()
        self.assert_no_artifact(task)

    def test_empty_repository_evidence_blocks_even_with_new_file_permission(self):
        self.context.update(files={}, new_files=["docs/new-guide.md"])
        task = self.run_work([])
        self.assertEqual(task["status"], "blocked")
        self.model.generate.assert_not_called()
        self.assert_no_artifact(task)

    def test_blank_repository_evidence_blocks_before_any_model_call(self):
        self.context.update(files={"README.md": " \n\t", "docs/empty.md": ""},
                            new_files=["docs/new-guide.md"])
        task = self.run_work([])
        self.assertEqual(task["status"], "blocked")
        self.model.generate.assert_not_called()
        self.assert_no_artifact(task)

    def test_infeasible_plan_preserves_explanation_without_writing(self):
        explanation = "The guide needs unavailable runtime evidence."
        task = self.run_work([{"feasible": False, "summary": explanation,
                               "steps": [], "source_paths": [], "target_paths": []}])
        self.assertEqual(task["status"], "blocked")
        self.assertIn(explanation, task["reason"])
        self.assertEqual(self.model.generate.call_count, 1)
        self.assertEqual(self.model.generate.call_args.args[0], PREPARE_SYSTEM)
        self.assert_no_artifact(task)

    def test_malformed_or_unsupported_plans_never_reach_writer(self):
        cases = [None, [], {"feasible": True},
                 work_plan() | {"feasible": "true"},
                 work_plan() | {"summary": "  "},
                 work_plan() | {"steps": []},
                 work_plan() | {"steps": ["  "]},
                 work_plan() | {"steps": ["step"] * 9},
                 work_plan() | {"source_paths": []},
                 work_plan() | {"source_paths": ["docs/unread.md"]},
                 work_plan() | {"source_paths": "README.md"},
                 work_plan() | {"target_paths": []},
                 work_plan() | {"target_paths": ["docs/not-approved.md"]},
                 work_plan() | {"target_paths": ["docs/../README.md"]},
                 work_plan() | {"target_paths": ["docs/new.md"] * 7}]
        for proposed in cases:
            with self.subTest(plan=proposed):
                self.store.update_task(self.task_id, status="qualified")
                self.model.reset_mock()
                task = self.run_work([proposed])
                self.assertEqual(task["status"], "blocked")
                self.assertEqual(self.model.generate.call_count, 1)
                self.assertEqual(self.model.generate.call_args.args[0], PREPARE_SYSTEM)
                self.assert_no_artifact(task)

    def test_target_limit_applies_even_to_seven_existing_paths(self):
        self.context["files"].update({f"docs/guide-{index}.md": "# Existing\n" for index in range(6)})
        task = self.run_work([work_plan(paths=list(self.context["files"]))])
        self.assertEqual(task["status"], "blocked")
        self.assertEqual(self.model.generate.call_count, 1)
        self.assert_no_artifact(task)

    def test_reviewer_feedback_is_revised_once_and_independently_accepted(self):
        first = delivery()
        fixed = delivery()
        fixed["files"][0]["content"] += "\nThe option selects the documented example.\n"
        rejected = {"accepted": False, "findings": ["Explain what the documented option selects."]}
        task = self.run_work([work_plan(), first, rejected, fixed, {"accepted": True, "findings": []}])

        self.assertEqual(task["status"], "ready")
        calls = self.model.generate.call_args_list
        self.assertEqual([call.args[0] for call in calls],
                         [PREPARE_SYSTEM, WRITER_SYSTEM, REVIEW_SYSTEM, WRITER_SYSTEM, REVIEW_SYSTEM])
        self.assertEqual(calls[3].args[1]["review_feedback"],
                         {"findings": rejected["findings"], "previous_delivery": first})
        self.assertEqual(calls[4].args[1]["proposed_delivery"], fixed)
        manifests = list((Path(self.temp.name) / "artifacts").glob("*/*/delivery.json"))
        self.assertEqual(len(manifests), 2)
        saved = [json.loads(path.read_text()) for path in manifests]
        self.assertEqual(sum(item["review"]["accepted"] for item in saved), 1)
        final = json.loads((Path(task["artifact_path"]).parent / "delivery.json").read_text())
        self.assertEqual(final["delivery"], fixed)

    def test_second_rejection_keeps_both_drafts_and_blocks_without_more_calls(self):
        rejected = {"accepted": False, "findings": ["The requested evidence is still missing."]}
        task = self.run_work([work_plan(), delivery(), rejected, delivery(), rejected])
        self.assertEqual(task["status"], "blocked")
        self.assertIn(rejected["findings"][0], task["reason"])
        self.assertEqual(self.model.generate.call_count, 5)
        self.assertTrue(Path(task["artifact_path"]).is_file())
        self.assertEqual(len(list((Path(self.temp.name) / "artifacts").glob("*/*/delivery.json"))), 2)

    def test_reviewer_findings_require_revision_even_with_accepted_true(self):
        task = self.run_work([work_plan(), delivery(), {"accepted": True, "findings": ["Missing evidence."]},
                              delivery(), {"accepted": True, "findings": []}])
        self.assertEqual(task["status"], "ready")
        self.assertEqual(self.model.generate.call_count, 5)
        self.assertEqual(self.model.generate.call_args_list[3].args[1]["review_feedback"]["findings"], ["Missing evidence."])

    def test_new_document_requires_explicit_permission_and_uses_existing_evidence(self):
        path = "docs/new-guide.md"
        self.context["new_files"] = [path]
        new_delivery = delivery()
        new_delivery["files"][0]["path"] = path
        plan = work_plan(paths=[path])
        task = self.run_work([plan, new_delivery, {"accepted": True, "findings": []}])
        self.assertEqual(task["status"], "ready")
        self.assertNotIn(path, self.context["files"])
        writer = self.model.generate.call_args_list[1]
        self.assertEqual(writer.args[1]["editable_paths"], [path])
        enum = writer.kwargs["schema"]["properties"]["files"]["items"]["properties"]["path"]["enum"]
        self.assertEqual(enum, [path])
        self.assertEqual((Path(task["artifact_path"]).parent / "files" / path).read_text(), new_delivery["files"][0]["content"])

    def test_unpinned_new_document_cannot_pass_preparation(self):
        self.context.update(commit="main", new_files=["docs/new-guide.md"])
        task = self.run_work([work_plan(paths=["docs/new-guide.md"])])
        self.assertEqual(task["status"], "blocked")
        self.assertLessEqual(self.model.generate.call_count, 1)
        self.assert_no_artifact(task)

    def test_revision_cannot_invent_successful_tests_even_after_reviewer_feedback(self):
        revised = delivery()
        revised["summary"] = "All tests passed."
        task = self.run_work([work_plan(), delivery(), {"accepted": False, "findings": ["Clarify the example."]}, revised])
        self.assertEqual(task["status"], "blocked")
        self.assertIn("påstår att tester", task["reason"])
        self.assertEqual(self.model.generate.call_count, 4)
        # The rejected first draft remains available; the fabricated revision is not reviewed or saved.
        self.assertTrue(Path(task["artifact_path"]).is_file())
        self.assertEqual(len(list((Path(self.temp.name) / "artifacts").glob("*/*/delivery.json"))), 1)

    def test_pause_or_stop_after_each_stage_prevents_the_next_model_call(self):
        sequence = [work_plan(), delivery(), {"accepted": False, "findings": ["Clarify the example."]}, delivery()]
        for action in ("pause", "stop"):
            for stage in range(len(sequence)):
                with self.subTest(action=action, stage=stage):
                    self.engine._stop.clear()
                    self.store.update_settings({"running": True})
                    self.store.update_task(self.task_id, status="qualified", artifact_path=None)
                    self.model.reset_mock()
                    seen = []

                    def generate(system, data, **kwargs):
                        index = len(seen)
                        seen.append(system)
                        if index == stage:
                            if action == "pause":
                                self.store.update_settings({"running": False})
                            else:
                                self.engine._stop.set()
                        return copy.deepcopy(sequence[index])

                    task = self.run_work(generate)
                    self.assertEqual(task["status"], "qualified")
                    self.assertEqual(self.model.generate.call_count, stage + 1)

    def test_restart_requeues_preparation_without_generating_or_publishing(self):
        self.store.claim_task("worker-1")
        self.assertEqual(self.store.get_task(self.task_id)["status"], "preparing")
        self.store.update_settings({"running": False})
        with patch.object(self.engine, "_loop"):
            self.engine.start()
            self.engine._scheduler.join(timeout=2)
        self.assertEqual(self.store.get_task(self.task_id)["status"], "discovered")
        self.assertFalse(self.github.method_calls)
        self.model.generate.assert_not_called()

    def test_legacy_new_document_block_is_requeued_once_without_artifacts_or_network(self):
        old_reason = ("Modellen föreslog filer utanför det hämtade underlaget: docs/recovery.md. "
                      "Denna version kan bara ändra de inlästa dokumenten.")
        self.store.update_settings({"running": False})
        self.store.update_task(self.task_id, status="blocked", reason=old_reason,
                               attempts=1, worker_id="worker-1")
        before = self.store.get_task(self.task_id)

        def restart_without_work():
            with patch.object(self.engine, "_loop"):
                self.engine.start()
                self.engine._scheduler.join(timeout=2)

        restart_without_work()

        after = self.store.get_task(self.task_id)
        self.assertEqual(after["status"], "discovered")
        self.assertTrue(after["new_document_recheck_v1"])
        for key in ("attempts", "worker_id", "url", "reward_amount", "reward_currency"):
            self.assertEqual(after[key], before[key])
        self.assert_no_artifact(after)
        self.store.update_task(self.task_id, status="blocked", reason=old_reason)
        protected = self.store.get_task(self.task_id)

        restart_without_work()

        self.assertEqual(self.store.get_task(self.task_id), protected)
        self.assertFalse(self.github.method_calls)
        self.model.generate.assert_not_called()

    def test_new_document_migration_preserves_existing_deliveries_and_prior_rechecks(self):
        old_reason = ("Modellen föreslog filer utanför det hämtade underlaget: docs/recovery.md. "
                      "Denna version kan bara ändra de inlästa dokumenten.")
        self.store.update_settings({"running": False})
        cases = [{"artifact_path": "/saved/delivery.md"},
                 {"submission_url": "https://github.com/owner/project/pull/9"},
                 {"new_document_recheck_v1": True}, {"writer_path_recheck_v1": True},
                 *({"status": status} for status in ("failed", "ready", "submitted", "awaiting_payment", "paid"))]
        protected = []
        for index, changes in enumerate(cases, start=20):
            candidate = qualify(issue(url=f"https://github.com/owner/project/issues/{index}", number=index))
            candidate.update(status="blocked", reason=old_reason, attempts=1)
            candidate.update(changes)
            task_id = self.store.upsert_task(candidate)
            protected.append(self.store.get_task(task_id))

        with patch.object(self.engine, "_loop"):
            self.engine.start()
            self.engine._scheduler.join(timeout=2)

        self.assertEqual([self.store.get_task(task["id"]) for task in protected], protected)
        self.assertFalse(self.github.method_calls)
        self.model.generate.assert_not_called()
        self.assertFalse((Path(self.temp.name) / "artifacts").exists())


class NewDocumentClaimBoundaryTests(unittest.TestCase):
    def test_only_pinned_allowed_new_documents_with_real_evidence_pass_claim_check(self):
        proposed = delivery()
        proposed["files"][0]["path"] = "docs/new-guide.md"
        valid = {"commit": "a" * 40, "new_files": ["docs/new-guide.md"], "files": {"README.md": "# Evidence\n"}}
        Engine._validate_claims(proposed, valid)
        invalid = [valid | {"commit": "a" * 7}, valid | {"commit": "g" * 40}, valid | {"commit": None},
                   valid | {"files": {}}, valid | {"files": {"README.md": " \n"}},
                   valid | {"new_files": []}, valid | {"new_files": "docs/new-guide.md"}]
        for context in invalid:
            with self.subTest(context=context), self.assertRaises(ConnectorError):
                Engine._validate_claims(proposed, context)
        unsafe = copy.deepcopy(proposed)
        unsafe["files"][0]["path"] = "docs/../../secret.md"
        with self.assertRaises(ConnectorError):
            Engine._validate_claims(unsafe, valid | {"new_files": [unsafe["files"][0]["path"]]})


if __name__ == "__main__":
    unittest.main()
