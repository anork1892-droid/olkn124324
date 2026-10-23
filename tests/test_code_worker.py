import io
import json
import stat
import unittest
import zipfile
from unittest.mock import Mock

from bountyos.code_worker import CodeWorker, CodeWorkPaused, implementation_path, read_snapshot, safe_python_path
from bountyos.connectors import ConnectorError


ORIGINAL = {
    "calc.py": "def clamp(value):\n    return max(0, value)\n",
    "tests/test_calc.py": "import unittest\nfrom calc import clamp\n\nclass Tests(unittest.TestCase):\n    def test_positive(self):\n        self.assertEqual(clamp(4), 4)\n",
}


def archive(files, *, symlink=None):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output:
        for name, content in files.items():
            output.writestr("project-" + "a" * 40 + "/" + name, content)
        if symlink:
            info = zipfile.ZipInfo("project-" + "a" * 40 + "/" + symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(info, "/host/file")
    return target.getvalue()


def report(count=1, *, failures=0, errors=0, skipped=0, timeout=False):
    return {"passed": not (failures or errors or timeout), "tests_run": count,
            "failure_count": failures, "error_count": errors, "skipped_count": skipped,
            "timed_out": timeout, "output": "actual sandbox result", "detail": ""}


class SnapshotTests(unittest.TestCase):
    def test_reads_python_without_extracting_ignored_configuration(self):
        files = {**ORIGINAL, "README.md": "# Python example", ".github/workflows/test.yml": "run: dangerous"}
        self.assertEqual(read_snapshot(archive(files)), ORIGINAL)

    def test_rejects_unsafe_archive_paths_even_for_non_python(self):
        for path in ("../outside.txt", "/absolute.txt", "docs/../outside.txt", "C:/outside.txt", "dir\\outside.txt"):
            with self.subTest(path=path), self.assertRaises(ConnectorError):
                read_snapshot(archive({**ORIGINAL, path: "ignored"}))

    def test_rejects_symlink_even_when_not_python(self):
        with self.assertRaisesRegex(ConnectorError, "länk"):
            read_snapshot(archive(ORIGINAL, symlink="link.txt"))

    def test_rejects_duplicate_casefold_paths(self):
        with self.assertRaisesRegex(ConnectorError, "dubbla"):
            read_snapshot(archive({**ORIGINAL, "Calc.py": "pass"}))

    def test_requires_existing_tests_and_standard_library(self):
        with self.assertRaisesRegex(ConnectorError, "befintliga unittest"):
            read_snapshot(archive({"calc.py": "pass"}))
        with self.assertRaisesRegex(ConnectorError, "Externt beroende"):
            read_snapshot(archive({**ORIGINAL, "calc.py": "import requests"}))

    def test_rejects_size_and_file_count(self):
        with self.assertRaisesRegex(ConnectorError, "för stor"):
            read_snapshot(archive({**ORIGINAL, "calc.py": "#" + "x" * 28000}))
        with self.assertRaisesRegex(ConnectorError, "för stor"):
            read_snapshot(archive({**ORIGINAL, **{f"module_{i}.py": "pass" for i in range(30)}}))

    def test_no_source_is_imported_on_host(self):
        # ast.parse is the only interpretation performed by read_snapshot.
        files = {**ORIGINAL, "calc.py": "raise RuntimeError('must never execute')\n"}
        self.assertEqual(read_snapshot(archive(files)), files)

    def test_path_policy_reserves_tests_scripts_and_sensitive_code(self):
        self.assertTrue(implementation_path("src/example/parser.py"))
        for path in ("../x.py", "test_calc.py", "tests/test_calc.py", "scripts/run.py", "setup.py", "auth.py", "pkg/payment_service.py", "pkg/security.py", "pkg/CON.py"):
            with self.subTest(path=path):
                self.assertFalse(implementation_path(path))
        self.assertFalse(safe_python_path("pkg/.hidden.py"))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.issue = {"url": "https://github.com/owner/project/issues/1", "repo": "owner/project",
                      "title": "Clamp values at ten", "body": "Repair clamp so values above ten return ten."}
        root = {"payload": {"repo": {"defaultBranch": "main"}, "codeViewRepoRoute": {
            "path": "/", "refInfo": {"name": "main", "currentOid": "a" * 40}}}}
        self.github = Mock()
        self.github.http.get.return_value = json.dumps(root)
        self.generate = Mock()
        self.sandbox = Mock()
        self.sandbox.check.return_value = {"configured": True, "status": "connected"}
        self.worker = CodeWorker(self.github, self.generate, self.sandbox)
        self.worker._download_snapshot = Mock(return_value=archive(ORIGINAL))
        self.context = self.worker.context(self.issue)
        self.regression_path = self.context["regression_path"]
        self.plan = {"feasible": True, "summary": "Clamp upper bound and test overflow", "steps": ["Clamp values above ten", "Add regression"],
                     "source_paths": list(ORIGINAL), "target_paths": ["calc.py", self.regression_path]}
        self.delivery = {"files": [
            {"path": "calc.py", "content": "def clamp(value):\n    return min(10, max(0, value))\n"},
            {"path": self.regression_path, "content": "import unittest\nfrom calc import clamp\n\nclass Regression(unittest.TestCase):\n    def test_upper_bound(self):\n        self.assertEqual(clamp(15), 10)\n"},
        ], "summary": "Clamp values above ten and add a regression test."}
        self.review = {"accepted": True, "findings": []}
        self.generate.side_effect = [self.plan, self.delivery, self.review]
        self.sandbox.run.side_effect = [report(), report(2, failures=1), report(2)]

    def test_context_download_is_pinned_and_keeps_existing_tests_immutable(self):
        self.worker._download_snapshot.assert_called_with("owner/project", "a" * 40)
        self.assertEqual(self.context["commit"], "a" * 40)
        self.assertNotIn("tests/test_calc.py", self.context["editable_paths"])

    def test_no_unpinned_fallback(self):
        self.github.http.get.return_value = json.dumps({"defaultBranch": "main"})
        self.worker._download_snapshot.reset_mock()
        with self.assertRaisesRegex(ConnectorError, "exakt Git-version"):
            self.worker.context(self.issue)
        self.worker._download_snapshot.assert_not_called()

    def test_actual_baseline_regression_and_final_are_all_required(self):
        stages = []
        result = self.worker.execute(self.issue, on_stage=lambda stage, payload: stages.append(stage))
        self.assertEqual(result["delivery"], self.delivery)
        self.assertTrue(result["test_report"]["baseline"]["passed"])
        self.assertFalse(result["test_report"]["regression_before"]["passed"])
        self.assertTrue(result["test_report"]["final"]["passed"])
        runs = self.sandbox.run.call_args_list
        self.assertEqual(runs[0].args[0], ORIGINAL)
        self.assertEqual(runs[1].args[0]["calc.py"], ORIGINAL["calc.py"])
        self.assertEqual(runs[2].args[0]["calc.py"], self.delivery["files"][0]["content"])
        self.assertEqual(runs[2].args[0]["tests/test_calc.py"], ORIGINAL["tests/test_calc.py"])
        self.assertEqual(stages[-1], "complete")

    def test_missing_docker_stops_before_download_or_model(self):
        self.worker._download_snapshot.reset_mock()
        self.sandbox.check.return_value = {"configured": False, "status": "error", "detail": "Docker saknas"}
        with self.assertRaisesRegex(ConnectorError, "Docker saknas"):
            self.worker.execute(self.issue)
        self.worker._download_snapshot.assert_not_called()
        self.generate.assert_not_called()

    def test_broken_empty_or_skipped_baseline_stops_before_model(self):
        for baseline in (report(0), report(1, failures=1), report(1, errors=1), report(2, skipped=1)):
            with self.subTest(baseline=baseline):
                self.sandbox.run.side_effect = [baseline]
                with self.assertRaisesRegex(ConnectorError, "befintliga tester måste"):
                    self.worker.execute(self.issue)
        self.generate.assert_not_called()

    def test_import_error_is_not_regression_evidence(self):
        self.generate.side_effect = [self.plan, self.delivery, self.delivery]
        self.sandbox.run.side_effect = [report(), report(2, errors=1), report(2, errors=1)]
        with self.assertRaisesRegex(ConnectorError, "verklig assertion"):
            self.worker.execute(self.issue)
        self.assertEqual(self.generate.call_count, 3)
        self.assertEqual(self.sandbox.run.call_count, 3)

    def test_passing_original_is_not_regression_evidence(self):
        self.generate.side_effect = [self.plan, self.delivery, self.delivery]
        self.sandbox.run.side_effect = [report(), report(2), report(2)]
        with self.assertRaisesRegex(ConnectorError, "verklig assertion"):
            self.worker.execute(self.issue)

    def test_failed_repair_gets_only_one_retry_using_actual_feedback(self):
        self.generate.side_effect = [self.plan, self.delivery, self.delivery, self.review]
        self.sandbox.run.side_effect = [report(), report(2, failures=1), report(2, failures=1), report(2, failures=1), report(2)]
        result = self.worker.execute(self.issue)
        self.assertEqual(result["test_report"]["attempts"], 2)
        feedback = self.generate.call_args_list[2].args[1]["test_feedback"]
        self.assertEqual(feedback["test_report"]["final"]["failure_count"], 1)

    def test_independent_review_can_reject_passing_tests(self):
        rejected = {"accepted": False, "findings": ["Does not cover all requirements."]}
        self.generate.side_effect = [self.plan, self.delivery, rejected, self.delivery, rejected]
        self.sandbox.run.side_effect = [report(), report(2, failures=1), report(2), report(2, failures=1), report(2)]
        with self.assertRaisesRegex(ConnectorError, "Does not cover"):
            self.worker.execute(self.issue)

    def test_cannot_change_existing_tests_or_create_other_code(self):
        for path in ("tests/test_calc.py", "other.py", "../calc.py"):
            invalid = {**self.delivery, "files": [{"path": path, "content": "pass"}, self.delivery["files"][1]]}
            self.generate.side_effect = [self.plan, invalid, invalid]
            self.sandbox.run.side_effect = [report()]
            with self.subTest(path=path), self.assertRaisesRegex(ConnectorError, "utanför arbetsplanen"):
                self.worker.execute(self.issue)

    def test_cannot_weaken_selected_targets_in_model_plan(self):
        self.generate.side_effect = [{**self.plan, "target_paths": ["tests/test_calc.py", self.regression_path]}]
        with self.assertRaisesRegex(ConnectorError, "otillåtna"):
            self.worker.execute(self.issue)

    def test_pause_between_stages_prevents_later_model_and_test_calls(self):
        paused = False

        def finish_baseline(files):
            nonlocal paused
            paused = True
            return report()

        self.sandbox.run.side_effect = finish_baseline
        with self.assertRaises(CodeWorkPaused):
            self.worker.execute(self.issue, should_stop=lambda: paused)
        self.generate.assert_not_called()
        self.assertEqual(self.sandbox.run.call_count, 1)

    def test_change_size_is_bounded(self):
        invalid = {**self.delivery, "files": [self.delivery["files"][0], {"path": self.regression_path, "content": "# comment\n" * 151}]}
        self.generate.side_effect = [self.plan, invalid, invalid]
        self.sandbox.run.side_effect = [report()]
        with self.assertRaisesRegex(ConnectorError, "150 ändrade rader"):
            self.worker.execute(self.issue)


if __name__ == "__main__":
    unittest.main()
