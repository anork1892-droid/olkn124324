"""Source ownership and specialist orchestration with offline connector/worker fixtures."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from bountyos.app import SettingsUpdate
from bountyos.code_worker import CodeWorkPaused
from bountyos.connectors import ConnectorError, GitHub, HTTPFailure, RateLimited, qualify
from bountyos.engine import Engine
from bountyos.store import Store
from test_engine import delivery, issue, work_plan


def code_result(url="https://github.com/owner/project/issues/7"):
    regression_path = "tests/test_bountyos_regression_" + hashlib.sha256(url.encode()).hexdigest()[:12] + ".py"
    baseline = {"passed": True, "tests_run": 1, "failure_count": 0, "error_count": 0, "skipped_count": 0}
    regression = {**baseline, "passed": False, "tests_run": 2, "failure_count": 1}
    final = {**baseline, "tests_run": 2}
    plan = {"summary": "Handle empty values.", "steps": ["Add a regression and handle the empty input."],
            "source_paths": ["example.py"], "target_paths": ["example.py", regression_path]}
    return {
        "context": {"repo": "owner/project", "commit": "a" * 40, "kind": "code",
                    "files": {"example.py": "def empty(value): return False\n",
                              "tests/test_existing.py": "import unittest\nfrom example import empty\n\nclass ExistingTest(unittest.TestCase):\n    def test_nonempty(self):\n        self.assertFalse(empty('value'))\n"},
                    "regression_path": regression_path, "new_files": [regression_path],
                    "editable_paths": ["example.py", regression_path]},
        "delivery": {"summary": "Handle empty values with a regression test.", "files": [
            {"path": "example.py", "content": "def empty(value): return value == ''\n"},
            {"path": regression_path, "content": "import unittest\nfrom example import empty\n\nclass EmptyTest(unittest.TestCase):\n    def test_empty(self):\n        self.assertTrue(empty(''))\n"}]},
        "review": {"accepted": True, "findings": []}, "work_plan": plan,
        "test_report": {"runner": "Docker fixture", "baseline": baseline, "regression_before": regression,
                        "final": final, "attempts": 1, "regression_path": regression_path},
    }


class BountyHubWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.update_settings({"running": True, "search_enabled": False})
        self.issues = {}
        self.github = Mock()
        self.github.search.return_value = []
        self.github.issue.side_effect = lambda url: copy.deepcopy(self.issues[url])
        self.github.repository_context.return_value = {"repo": "owner/project", "branch": "main",
                                                        "files": {"README.md": "# Existing example\n"}}
        self.bountyhub = Mock()
        self.bountyhub.search.return_value = []
        self.bountyhub.issue.side_effect = lambda url: copy.deepcopy(self.issues[url])
        self.bountyhub.qualify.side_effect = self.source_candidate
        self.bountyhub.http.retry_after_seconds.return_value = 0
        self.model = Mock()
        self.model.model = "fixture:3b"
        self.model.generate.side_effect = [work_plan(), delivery(), {"accepted": True, "findings": []}]
        self.submitter = Mock(configured=True)
        self.submitter.submit.return_value = "https://github.com/owner/project/pull/9"
        self.code_worker = Mock()
        self.code_worker.execute.return_value = code_result()
        self.engine = Engine(self.store, github=self.github, model=self.model, submitter=self.submitter,
                             bountyhub=self.bountyhub, code_worker=self.code_worker)
        self.addCleanup(self.engine.stop)

    def tearDown(self):
        metrics = self.store.get_state()["metrics"]
        self.assertEqual((metrics["earned_sek"], metrics["estimated_cost_sek"], metrics["net_sek"]), (0, 0, 0))
        self.assertEqual(self.store.get_settings()["daily_budget_sek"], 0)

    @staticmethod
    def source_candidate(raw, **settings):
        candidate = qualify(raw)
        candidate.update(source="bountyhub", specialist=raw.get("fixture_specialist", "documentation"),
                         kind=raw.get("fixture_specialist", "documentation"),
                         status=raw.get("fixture_status", "qualified"), reason="BountyHub source assessment.",
                         bountyhub_url="https://www.bountyhub.dev/bounty/view/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        return candidate

    def add_task(self, number=7, specialist="documentation", source="bountyhub", status="qualified", **changes):
        raw = issue(url=f"https://github.com/owner/project/issues/{number}", number=number,
                    fixture_specialist=specialist)
        if specialist == "code":
            raw.update(title="Python bounty: fix empty-value bug, 25 USD", body="Fix the existing Python helper. Pays 25 USD after acceptance.")
        self.issues[raw["url"]] = raw
        candidate = self.source_candidate(raw)
        candidate.update(status=status, **changes)
        if source is None:
            candidate.pop("source")
            candidate.pop("specialist")
        else:
            candidate["source"] = source
        return self.store.upsert_task(candidate)

    def scan_bountyhub(self):
        self.assertTrue(self.engine._bountyhub_scan_lock.acquire(blocking=False))
        self.engine._scan_bountyhub()
        self.assertFalse(self.engine._bountyhub_scan_lock.locked())

    def run_work(self, task_id):
        task = self.store.get_task(task_id)
        agent = "bountyhub-code" if task.get("specialist") == "code" else "bountyhub-docs"
        task = self.store.claim_task(agent, eligible=lambda item: item["id"] == task_id)
        self.engine._work(agent, task)
        return self.store.get_task(task_id)

    def test_existing_github_fakes_do_not_implicitly_activate_new_source(self):
        engine = Engine(self.store, github=self.github, model=self.model, submitter=self.submitter)
        self.addCleanup(engine.stop)
        self.assertIsNone(engine.bountyhub)
        self.assertNotIn("bountyhub", engine.connection_status())

    def test_disabled_ready_specialist_does_not_block_other_deliveries(self):
        docs_id = self.add_task(90, status="ready")
        code_id = self.add_task(91, status="ready", specialist="code")
        self.store.update_settings({"auto_submit": True, "bountyhub_code_enabled": False})
        self.assertEqual(self.engine._next_ready_task(self.store.get_settings())["id"], docs_id)
        self.engine._submit_ready(self.store.get_task(code_id))
        self.github.issue.assert_not_called()
        self.bountyhub.qualify.assert_not_called()
        self.submitter.submit.assert_not_called()

    def test_default_source_reuses_transport_but_keeps_its_own_search_cursor(self):
        with patch("bountyos.engine.GitHub") as github_type:
            github_type.return_value = self.github
            engine = Engine(self.store, model=self.model, submitter=self.submitter)
        self.addCleanup(engine.stop)
        self.assertIs(engine.bountyhub.http, self.github.http)
        self.assertNotEqual(engine.bountyhub.SEARCH_STATE_KEY, GitHub.SEARCH_STATE_KEY)

    def test_search_deduplicates_and_promotes_untouched_general_rejections(self):
        ids = [self.add_task(number, source=None, status=status)
               for number, status in enumerate(("discovered", "blocked", "skipped"), start=10)]
        urls = [self.store.get_task(task_id)["url"] for task_id in ids]
        self.bountyhub.search.return_value = urls + urls
        self.scan_bountyhub()
        self.scan_bountyhub()
        self.assertEqual(len(self.store.list_tasks()), 3)
        for task_id in ids:
            task = self.store.get_task(task_id)
            self.assertEqual((task["source"], task["specialist"], task["status"]),
                             ("bountyhub", "documentation", "qualified"))
        self.assertEqual(self.bountyhub.issue.call_count, 3)
        self.assertEqual(self.bountyhub.qualify.call_count, 3)
        for call in self.bountyhub.qualify.call_args_list:
            self.assertEqual(call.kwargs, {"max_sek": 1500, "assume_usd": True})
        self.github.issue.assert_not_called()
        self.model.generate.assert_not_called()

    def test_source_search_never_restarts_worked_tasks_or_overwrites_deliveries(self):
        cases = [{"attempts": 1}, {"worker_id": "worker-1"}, {"artifact_path": "/saved/delivery.md"},
                 {"submission_url": "https://github.com/owner/project/pull/9"},
                 *({"status": status} for status in ("qualified", "preparing", "working", "testing", "reviewing", "ready", "submitted", "paid"))]
        ids = []
        for number, changes in enumerate(cases, start=30):
            changes = {"status": "blocked", **changes}
            ids.append(self.add_task(number, source=None, **changes))
        before = [self.store.get_task(task_id) for task_id in ids]
        self.bountyhub.search.return_value = [task["url"] for task in before]
        self.scan_bountyhub()
        self.assertEqual([self.store.get_task(task_id) for task_id in ids], before)
        self.bountyhub.issue.assert_not_called()
        self.model.generate.assert_not_called()
        self.code_worker.execute.assert_not_called()

    def test_generic_scan_leaves_bountyhub_pending_and_qualified_tasks_to_source(self):
        ids = [self.add_task(50, status="discovered"), self.add_task(51)]
        before = [self.store.get_task(task_id) for task_id in ids]
        self.github.search.return_value = [task["url"] for task in before]
        self.engine._scan_lock.acquire()
        self.engine._scan()
        self.assertEqual([self.store.get_task(task_id) for task_id in ids], before)
        self.github.issue.assert_not_called()
        self.bountyhub.qualify.assert_not_called()

    def test_generic_scan_cannot_overwrite_source_promotion_during_network_read(self):
        task_id = self.add_task(52, source=None, status="discovered")
        raw = self.issues[self.store.get_task(task_id)["url"]]
        raw["authorAssociation"] = "NONE"
        self.bountyhub.search.return_value = [raw["url"]]

        def promote_while_reading(url):
            self.scan_bountyhub()
            return copy.deepcopy(raw)

        self.github.issue.side_effect = promote_while_reading
        self.engine._scan_lock.acquire()
        self.engine._scan()
        task = self.store.get_task(task_id)
        self.assertEqual((task["source"], task["status"], task["reason"]),
                         ("bountyhub", "qualified", "BountyHub source assessment."))
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_scan_persists_all_discoveries_but_assesses_at_most_twelve(self):
        urls = []
        for number in range(100, 120):
            raw = issue(url=f"https://github.com/owner/project/issues/{number}", number=number)
            self.issues[raw["url"]] = raw
            urls.append(raw["url"])
        self.bountyhub.search.return_value = urls
        self.scan_bountyhub()
        tasks = self.store.list_tasks()
        self.assertEqual(len(tasks), 20)
        self.assertEqual(sum(task["status"] == "qualified" for task in tasks), 12)
        self.assertEqual(sum(task["status"] == "discovered" for task in tasks), 8)
        self.assertEqual(self.bountyhub.issue.call_count, 12)
        self.scan_bountyhub()
        self.assertEqual(self.bountyhub.issue.call_count, 20)
        self.assertTrue(all(task["status"] == "qualified" for task in self.store.list_tasks()))

    def test_deleted_pending_issue_does_not_starve_new_discovery(self):
        old_id = self.add_task(80, status="discovered")
        new_issue = issue(url="https://github.com/owner/project/issues/81", number=81)
        self.bountyhub.search.return_value = [new_issue["url"]]
        self.bountyhub.issue.side_effect = lambda url: (_ for _ in ()).throw(HTTPFailure(404)) if url.endswith("/80") else new_issue
        self.scan_bountyhub()
        self.assertEqual(self.store.get_task(old_id)["status"], "blocked")
        new = next(task for task in self.store.list_tasks() if task["url"] == new_issue["url"])
        self.assertEqual(new["status"], "qualified")
        self.bountyhub.search.assert_called_once()

    def test_transient_pending_issue_error_does_not_starve_new_discovery(self):
        old_id = self.add_task(82, status="discovered")
        new_issue = issue(url="https://github.com/owner/project/issues/83", number=83)
        self.bountyhub.search.return_value = [new_issue["url"]]
        self.bountyhub.issue.side_effect = lambda url: (_ for _ in ()).throw(ConnectorError("Tillfälligt läsfel")) if url.endswith("/82") else new_issue
        self.scan_bountyhub()
        self.assertEqual(self.store.get_task(old_id)["status"], "discovered")
        self.assertTrue(any(task["url"] == new_issue["url"] and task["status"] == "qualified" for task in self.store.list_tasks()))

    def test_rate_limit_preserves_discovery_and_sets_delayed_retry(self):
        raw = issue()
        self.issues[raw["url"]] = raw
        self.bountyhub.search.return_value = [raw["url"]]
        self.bountyhub.issue.side_effect = RateLimited("Retry later")
        self.bountyhub.http.retry_after_seconds.return_value = 300
        with patch("bountyos.bountyhub_workflow.time.monotonic", return_value=100):
            self.scan_bountyhub()
        task = self.store.list_tasks()[0]
        self.assertEqual(task["status"], "discovered")
        self.assertIn("Retry later", task["reason"])
        self.assertEqual(self.engine._next_bountyhub_scan, 400)
        self.assertEqual(self.engine.connection_status()["bountyhub"]["status"], "error")

    def test_search_page_is_queued_before_first_issue_read_can_fail(self):
        urls = [f"https://github.com/owner/project/issues/{number}" for number in range(130, 150)]
        self.bountyhub.search.return_value = urls
        self.bountyhub.issue.side_effect = RateLimited("Temporarily paused")
        self.scan_bountyhub()
        tasks = self.store.list_tasks()
        self.assertEqual({task["url"] for task in tasks}, set(urls))
        self.assertTrue(all(task["status"] == "discovered" for task in tasks))
        self.bountyhub.issue.assert_called_once()

    def test_source_disabled_prevents_starting_scan(self):
        self.store.update_settings({"bountyhub_enabled": False})
        with patch("bountyos.bountyhub_workflow.threading.Thread") as thread:
            self.engine._bountyhub_scan_now()
        thread.assert_not_called()
        self.bountyhub.search.assert_not_called()

    def test_docs_rechecks_source_before_work_and_before_submission(self):
        task_id = self.add_task()
        self.issues[self.store.get_task(task_id)["url"]]["authorAssociation"] = "NONE"
        self.store.update_settings({"auto_submit": True})
        task = self.run_work(task_id)
        self.assertEqual(task["status"], "submitted")
        self.assertEqual(self.bountyhub.qualify.call_count, 2)
        self.assertEqual(self.github.issue.call_count, 2)
        self.assertEqual(self.model.generate.call_count, 3)
        self.submitter.submit.assert_called_once()

    def test_changed_source_eligibility_blocks_publication_after_doc_review(self):
        task_id = self.add_task()
        raw = self.issues[self.store.get_task(task_id)["url"]]
        candidate = self.source_candidate(raw)
        self.bountyhub.qualify.side_effect = [candidate, {**candidate, "status": "blocked", "reason": "Bounty was withdrawn."}]
        self.store.update_settings({"auto_submit": True})
        task = self.run_work(task_id)
        self.assertEqual(task["status"], "blocked")
        self.assertIn("withdrawn", task["reason"])
        self.assertTrue(Path(task["artifact_path"]).is_file())
        self.submitter.submit.assert_not_called()

    def test_changed_issue_requirements_block_publication_after_review(self):
        task_id = self.add_task()
        original = self.issues[self.store.get_task(task_id)["url"]]
        self.github.issue.side_effect = [copy.deepcopy(original), {**original, "body": original["body"] + " Also explain the new authentication scheme."}]
        self.store.update_settings({"auto_submit": True})
        task = self.run_work(task_id)
        self.assertEqual(task["status"], "blocked")
        self.assertIn("krav har ändrats", task["reason"])
        self.submitter.submit.assert_not_called()

    def test_platform_requirements_reach_both_specialists(self):
        for number, specialist in ((92, "documentation"), (93, "code")):
            with self.subTest(specialist=specialist):
                task_id = self.add_task(number, specialist=specialist)
                raw = self.issues[self.store.get_task(task_id)["url"]]
                candidate = {**self.source_candidate(raw), "bountyhub_requirements": "Preserve the existing command examples."}
                self.bountyhub.qualify.side_effect = None
                self.bountyhub.qualify.return_value = candidate
                if specialist == "code":
                    self.code_worker.execute.return_value = code_result(raw["url"])
                task = self.run_work(task_id)
                self.assertEqual(task["status"], "ready")
                work_issue = self.code_worker.execute.call_args.args[0] if specialist == "code" else self.model.generate.call_args_list[0].args[1]["issue"]
                self.assertIn(candidate["bountyhub_requirements"], work_issue["body"])

    def test_changed_platform_requirements_block_publication(self):
        task_id = self.add_task()
        raw = self.issues[self.store.get_task(task_id)["url"]]
        candidate = {**self.source_candidate(raw), "bountyhub_requirements": "Preserve existing examples."}
        self.bountyhub.qualify.side_effect = [candidate, {**candidate, "bountyhub_requirements": "Document an additional configuration option."}]
        self.store.update_settings({"auto_submit": True})
        task = self.run_work(task_id)
        self.assertEqual(task["status"], "blocked")
        self.assertIn("krav har ändrats", task["reason"])
        self.submitter.submit.assert_not_called()

    def test_source_rejection_prevents_doc_or_code_generation(self):
        for number, specialist in enumerate(("documentation", "code"), start=150):
            with self.subTest(specialist=specialist):
                task_id = self.add_task(number, specialist=specialist)
                self.issues[self.store.get_task(task_id)["url"]]["fixture_status"] = "blocked"
                task = self.run_work(task_id)
                self.assertEqual(task["status"], "blocked")
                self.assertEqual(task["reason"], "BountyHub source assessment.")
        self.model.generate.assert_not_called()
        self.code_worker.execute.assert_not_called()
        self.submitter.submit.assert_not_called()

    def test_claim_filter_skips_disabled_specialists_and_unavailable_code_runner(self):
        code_id = self.add_task(160, specialist="code")
        docs_id = self.add_task(161)
        general_id = self.add_task(162, source=None)
        settings = self.store.get_settings()
        for status in ("unknown", "blocked", "error"):
            self.engine._connection("code_tests", {"status": status})
            self.assertFalse(self.engine._worker_eligible(self.store.get_task(code_id), settings))
        self.engine._connection("code_tests", {"status": "connected"})
        self.assertTrue(self.engine._worker_eligible(self.store.get_task(code_id), settings))
        settings.update(bountyhub_code_enabled=False, bountyhub_docs_enabled=False)
        selected = self.store.claim_task("worker-1", eligible=lambda task: self.engine._worker_eligible(task, settings))
        self.assertEqual(selected["id"], general_id)
        self.assertEqual(self.store.get_task(code_id)["status"], "qualified")
        self.assertEqual(self.store.get_task(docs_id)["status"], "qualified")
        settings.update(bountyhub_code_enabled=True, bountyhub_docs_enabled=True, bountyhub_enabled=False)
        self.assertIsNone(self.store.claim_task("worker-2", eligible=lambda task: self.engine._worker_eligible(task, settings)))

    def test_code_stage_progress_and_saved_test_report_do_not_auto_publish(self):
        task_id = self.add_task(specialist="code")
        result = code_result()
        observed = []

        def execute(raw, *, should_stop, on_stage):
            for stage in ("preparation", "writing", "testing", "review"):
                self.assertFalse(should_stop())
                on_stage(stage, {"work_plan": result["work_plan"], "test_report": result["test_report"]})
                current = self.store.get_task(task_id)
                observed.append(current["status"])
                agent = next(agent for agent in self.store.list_agents() if agent["id"] == "bountyhub-code")
                self.assertEqual(agent["status"], current["status"])
            return result

        self.code_worker.execute.side_effect = execute
        self.store.update_settings({"auto_submit": False})
        task = self.run_work(task_id)
        self.assertEqual(observed, ["preparing", "working", "testing", "reviewing"])
        self.assertEqual(task["status"], "ready")
        self.assertEqual(task["test_report"], result["test_report"])
        self.assertEqual(task["work_plan"], result["work_plan"])
        self.assertIn("inlämning", task["reason"])
        manifest = Path(task["artifact_path"])
        self.assertTrue(manifest.is_file())
        saved = json.loads((manifest.parent / "delivery.json").read_text())
        self.assertEqual(saved["kind"], "code")
        self.assertEqual(saved["test_report"], result["test_report"])
        self.assertEqual((manifest.parent / "files/example.py").read_text(), result["delivery"]["files"][0]["content"])
        self.engine._submit_ready(task)
        self.submitter.submit.assert_not_called()
        self.model.generate.assert_not_called()

    def test_enabled_code_publication_rechecks_source_and_passes_actual_report_contract(self):
        task_id = self.add_task(specialist="code")
        self.store.update_settings({"auto_submit": True})
        task = self.run_work(task_id)
        self.assertEqual(task["status"], "submitted")
        self.assertEqual(task["platform_claim_status"], "manual_required")
        self.assertEqual(self.bountyhub.qualify.call_count, 2)
        self.assertEqual(self.github.issue.call_count, 2)
        self.submitter.submit.assert_called_once()
        call = self.submitter.submit.call_args
        self.assertEqual(call.kwargs["test_report"], code_result()["test_report"])
        self.assertEqual(call.kwargs["work_plan"], code_result()["work_plan"])
        self.assertTrue(call.kwargs["may_publish"]())
        self.store.update_settings({"bountyhub_code_enabled": False})
        self.assertFalse(call.kwargs["may_publish"]())
        self.assertFalse(self.store.get_task(task_id).get("paid"))

    def test_failed_persisted_code_tests_prevent_publication_before_source_read(self):
        task_id = self.add_task(specialist="code")
        task = self.run_work(task_id)
        self.assertEqual(task["status"], "ready")
        envelope_path = Path(task["artifact_path"]).parent / "delivery.json"
        envelope = json.loads(envelope_path.read_text())
        envelope["test_report"]["final"]["passed"] = False
        envelope_path.write_text(json.dumps(envelope))
        self.github.issue.reset_mock()
        self.bountyhub.qualify.reset_mock()
        self.store.update_settings({"auto_submit": True})
        self.engine._submit_ready(task)
        self.assertEqual(self.store.get_task(task_id)["status"], "blocked")
        self.github.issue.assert_not_called()
        self.bountyhub.qualify.assert_not_called()
        self.submitter.submit.assert_not_called()

    def test_code_pause_or_disabled_specialist_returns_to_queue_without_artifact(self):
        for number, setting in enumerate(("running", "bountyhub_enabled", "bountyhub_code_enabled"), start=170):
            with self.subTest(setting=setting):
                self.store.update_settings({"running": True, "bountyhub_enabled": True, "bountyhub_code_enabled": True})
                task_id = self.add_task(number, specialist="code")

                def pause(raw, *, should_stop, on_stage):
                    on_stage("testing", {})
                    self.store.update_settings({setting: False})
                    self.assertTrue(should_stop())
                    raise CodeWorkPaused("Work paused.")

                self.code_worker.execute.side_effect = pause
                task = self.run_work(task_id)
                self.assertEqual(task["status"], "qualified")
                self.assertFalse(task.get("artifact_path"))
        self.submitter.submit.assert_not_called()

    def test_code_failures_preserve_the_stage_and_do_not_publish(self):
        for number, stage in enumerate(("preparation", "writing", "testing", "review"), start=180):
            with self.subTest(stage=stage):
                task_id = self.add_task(number, specialist="code")

                def fail(raw, *, should_stop, on_stage):
                    on_stage(stage, {})
                    raise ConnectorError("Specific test failure.")

                self.code_worker.execute.side_effect = fail
                task = self.run_work(task_id)
                self.assertEqual((task["status"], task["blocked_stage"]), ("blocked", stage))
                self.assertIn("Specific test failure", task["reason"])
                self.assertFalse(task.get("artifact_path"))
        self.submitter.submit.assert_not_called()

    def test_code_result_with_rejected_review_is_not_saved_as_ready(self):
        task_id = self.add_task(specialist="code")
        result = code_result()
        result["review"] = {"accepted": False, "findings": ["The code does not solve the issue."]}
        self.code_worker.execute.return_value = result
        task = self.run_work(task_id)
        self.assertEqual(task["status"], "blocked")
        self.assertFalse(task.get("artifact_path"))
        self.assertFalse((Path(self.temp.name) / "artifacts").exists())

    def test_restart_returns_interrupted_testing_to_discovery_without_new_work(self):
        task_id = self.add_task(specialist="code", status="testing", attempts=1)
        self.store.update_settings({"running": False})
        with patch.object(self.engine, "_loop"):
            self.engine.start()
            self.engine._scheduler.join(timeout=2)
        task = self.store.get_task(task_id)
        self.assertEqual((task["status"], task["source"], task["attempts"]), ("discovered", "bountyhub", 1))
        agents = {agent["id"] for agent in self.store.list_agents()}
        self.assertTrue({"bountyhub-scout", "bountyhub-docs", "bountyhub-code"} <= agents)
        self.github.issue.assert_not_called()
        self.model.generate.assert_not_called()
        self.code_worker.execute.assert_not_called()

    def test_one_shared_worker_starts_even_when_higher_agent_limit_is_set(self):
        self.add_task(190)
        self.add_task(191, specialist="code")
        self.store.update_settings({"max_agents": 5, "autoscale": False})
        self.engine._connection("ai", {"status": "connected"})
        self.engine._connection("code_tests", {"status": "connected"})
        self.engine._next_probe = self.engine._next_submission_check = float("inf")
        self.engine._stop = Mock()
        self.engine._stop.wait.side_effect = [False, True]
        self.engine._stop.is_set.return_value = False
        with patch("bountyos.engine.threading.Thread") as thread:
            self.engine._loop()
        self.assertEqual(thread.call_count, 1)
        thread.return_value.start.assert_called_once()
        self.assertEqual(sum(task["status"] == "preparing" for task in self.store.list_tasks()), 1)

    def test_local_model_calls_are_serialized_between_specialists(self):
        first_entered, release_first, second_entered = threading.Event(), threading.Event(), threading.Event()
        attempts = []

        def generate(system, data):
            attempts.append(system)
            if system == "first":
                first_entered.set()
                self.assertTrue(release_first.wait(2))
            else:
                second_entered.set()
            return {"ok": True}

        self.model.generate.side_effect = generate
        first = threading.Thread(target=self.engine._generate, args=("first", {}))
        second = threading.Thread(target=self.engine._generate, args=("second", {}))
        first.start()
        self.assertTrue(first_entered.wait(2))
        second.start()
        try:
            self.assertFalse(second_entered.wait(0.05))
        finally:
            release_first.set()
            first.join(timeout=2)
            second.join(timeout=2)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(attempts, ["first", "second"])


class BountyHubSettingsTests(unittest.TestCase):
    def test_defaults_persist_and_setting_validation_does_not_enable_paid_apis(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            defaults = store.get_settings()
            for name in ("bountyhub_enabled", "bountyhub_docs_enabled", "bountyhub_code_enabled", "bountyhub_assume_usd"):
                self.assertTrue(defaults[name])
                for invalid in (1, "true", None):
                    with self.subTest(name=name, value=invalid), self.assertRaises(ValueError):
                        store.update_settings({name: invalid})
                for invalid in (1, "true"):
                    with self.subTest(schema=name, value=invalid), self.assertRaises(ValidationError):
                        SettingsUpdate.model_validate({name: invalid})
            self.assertEqual(defaults["bountyhub_max_sek"], 1500)
            for invalid in (0, 100001, True, 2.5, "1500", None):
                with self.subTest(price=invalid), self.assertRaises(ValueError):
                    store.update_settings({"bountyhub_max_sek": invalid})
            for invalid in (0, 100001, True, 2.5, "1500"):
                with self.subTest(schema_price=invalid), self.assertRaises(ValidationError):
                    SettingsUpdate.model_validate({"bountyhub_max_sek": invalid})
            store.update_settings({"bountyhub_docs_enabled": False, "bountyhub_max_sek": 2000, "bountyhub_assume_usd": False})
            reloaded = Store(directory).get_settings()
            self.assertFalse(reloaded["bountyhub_docs_enabled"])
            self.assertFalse(reloaded["bountyhub_assume_usd"])
            self.assertEqual(reloaded["bountyhub_max_sek"], 2000)
            with self.assertRaises(ValueError):
                store.update_settings({"daily_budget_sek": 1})
            self.assertEqual(reloaded["daily_budget_sek"], 0)


if __name__ == "__main__":
    unittest.main()
