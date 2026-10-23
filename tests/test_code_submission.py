"""Optional Python-code publication; all GitHub and test-run responses are fixtures."""
import base64
import copy
import hashlib
import unittest
from unittest.mock import Mock

from bountyos.connectors import ConnectorError, GitHubSubmitter, HTTPFailure


SHA = "a" * 40
TASK = {"url": "https://github.com/owner/project/issues/7", "title": "Clamp values above ten",
        "source": "bountyhub", "specialist": "code", "kind": "code"}
REGRESSION = "tests/test_bountyos_regression_" + hashlib.sha256(TASK["url"].encode()).hexdigest()[:12] + ".py"
ORIGINAL = {
    "calc.py": "def clamp(value):\n    return max(0, value)\n",
    "tests/test_calc.py": "import unittest\nfrom calc import clamp\n\nclass Tests(unittest.TestCase):\n    def test_positive(self):\n        self.assertEqual(clamp(4), 4)\n",
}
CONTEXT = {"repo": "owner/project", "branch": "main", "commit": SHA, "kind": "code", "files": ORIGINAL,
           "new_files": [REGRESSION], "regression_path": REGRESSION, "editable_paths": ["calc.py", REGRESSION]}
PLAN = {"summary": "Clamp upper bound and test overflow", "steps": ["Clamp values above ten", "Add regression"],
        "source_paths": list(ORIGINAL), "target_paths": ["calc.py", REGRESSION]}
DELIVERY = {"files": [
    {"path": "calc.py", "content": "def clamp(value):\n    return min(10, max(0, value))\n"},
    {"path": REGRESSION, "content": "import unittest\nfrom calc import clamp\n\nclass Regression(unittest.TestCase):\n    def test_upper_bound(self):\n        self.assertEqual(clamp(15), 10)\n"},
], "summary": "Clamp values above ten and add a regression test."}


def run_report(count, failures=0, **changes):
    return {"passed": failures == 0, "tests_run": count, "failure_count": failures, "error_count": 0,
            "skipped_count": 0, "timed_out": False, **changes}


REPORT = {"runner": "Docker · Python 3.12 · unittest", "regression_path": REGRESSION,
          "baseline": run_report(1), "regression_before": run_report(2, failures=1), "final": run_report(2)}


def file_response(content):
    return {"type": "file", "encoding": "base64", "content": base64.b64encode(content.encode()).decode()}


class Fixture:
    def __init__(self):
        self.heads = [SHA, SHA, SHA]
        self.responses = {}
        self.submitter = GitHubSubmitter(http=Mock())
        self.submitter.api = Mock(side_effect=self.api)

    def api(self, path, **kwargs):
        key = (kwargs.get("method", "GET"), path)
        if key in self.responses:
            response = self.responses[key]
            if isinstance(response, Exception):
                raise response
            return response
        if path == "/user":
            return {"login": "me"}
        if path.startswith("/repos/owner/project/pulls?"):
            return []
        if path == "/repos/owner/project":
            return {"default_branch": "main"}
        if path == "/repos/owner/project/git/ref/heads/main":
            return {"object": {"sha": self.heads.pop(0)}}
        if path == f"/repos/owner/project/contents/{REGRESSION}?ref={SHA}":
            raise HTTPFailure(404)
        for filename, content in ORIGINAL.items():
            if path == f"/repos/owner/project/contents/{filename}?ref={SHA}":
                return file_response(content)
        if path == "/repos/owner/project/forks" and key[0] == "POST":
            return {"full_name": "me/project"}
        if path == "/repos/me/project/git/commits/" + SHA:
            return {"tree": {"sha": "base-tree"}}
        if path.startswith("/repos/me/project/git/ref/heads/"):
            raise HTTPFailure(404)
        if path == "/repos/me/project/git/trees" and key[0] == "POST":
            return {"sha": "changed-tree"}
        if path == "/repos/me/project/git/commits" and key[0] == "POST":
            return {"sha": "fix-commit"}
        if path == "/repos/me/project/git/refs" and key[0] == "POST":
            return {"ref": kwargs["payload"]["ref"]}
        if path == "/repos/owner/project/pulls" and key[0] == "POST":
            return {"html_url": "https://github.com/owner/project/pull/9"}
        raise AssertionError("Unexpected API call: " + repr(key))

    def submit(self, *, task=None, context=None, delivery=None, report=None, plan=None):
        return self.submitter.submit(task or TASK, CONTEXT if context is None else context,
                                     DELIVERY if delivery is None else delivery,
                                     test_report=REPORT if report is None else report,
                                     work_plan=PLAN if plan is None else plan)

    def writes(self):
        return [call for call in self.submitter.api.call_args_list if call.kwargs.get("method") == "POST"]


class CodeSubmissionTests(unittest.TestCase):
    def test_validated_code_and_new_regression_publish_as_fix_with_actual_test_counts(self):
        fixture = Fixture()
        self.assertTrue(fixture.submit().endswith("/pull/9"))
        calls = fixture.submitter.api.call_args_list
        first_write = next(index for index, call in enumerate(calls) if call.kwargs.get("method") == "POST")
        read_paths = [call.args[0] for call in calls[:first_write]]
        for filename in (*ORIGINAL, REGRESSION):
            self.assertIn(f"/repos/owner/project/contents/{filename}?ref={SHA}", read_paths)
        tree = next(call for call in fixture.writes() if call.args[0].endswith("/git/trees"))
        self.assertEqual([item["path"] for item in tree.kwargs["payload"]["tree"]], ["calc.py", REGRESSION])
        commit = next(call for call in fixture.writes() if call.args[0].endswith("/git/commits"))
        self.assertEqual(commit.kwargs["payload"]["parents"], [SHA])
        self.assertTrue(commit.kwargs["payload"]["message"].startswith("fix:"))
        pr = fixture.writes()[-1].kwargs["payload"]
        self.assertTrue(pr["title"].startswith("fix:"))
        self.assertIn(SHA, pr["body"])
        self.assertIn("Python 3.12 unittest in isolated Docker", pr["body"])
        self.assertIn("Baseline: 1 tests passed", pr["body"])
        self.assertIn("1 assertion failure(s), zero errors", pr["body"])
        self.assertIn("Repaired code: 2 tests passed", pr["body"])
        self.assertNotIn("No project tests were executed", pr["body"])
        self.assertIn("do not imply bounty acceptance or payment", pr["body"])

    def test_stale_tested_commit_is_not_rebased_even_when_files_are_unchanged(self):
        for heads in (["b" * 40], [SHA, "b" * 40]):
            with self.subTest(heads=heads):
                fixture = Fixture()
                fixture.heads = heads
                with self.assertRaisesRegex(ConnectorError, "Nytt arbete och nya tester"):
                    fixture.submit()
                self.assertEqual(fixture.writes(), [])

    def test_head_change_during_fork_writes_stops_before_pull_request(self):
        fixture = Fixture()
        fixture.heads = [SHA, SHA, "b" * 40]
        with self.assertRaisesRegex(ConnectorError, "Nytt arbete och nya tester"):
            fixture.submit()
        self.assertTrue(fixture.writes())
        self.assertFalse(any(call.args[0].endswith("/pulls") for call in fixture.writes()))

    def test_missing_plan_report_or_invalid_counters_fail_before_any_api_call(self):
        mutations = [{}, {"baseline": run_report(0)}, {"baseline": run_report(1, skipped_count=1)},
                     {"regression_before": run_report(2)}, {"regression_before": run_report(2, failures=1, error_count=1)},
                     {"regression_before": run_report(2, failures=1, timed_out=True)},
                     {"regression_before": run_report(1, failures=1)},
                     {"regression_before": run_report(2, failures=True)},
                     {"final": run_report(2, failures=1)}, {"final": run_report(1)},
                     {"final": run_report(2, expected_failure_count=1)},
                     {"regression_path": "tests/test_wrong.py"}]
        for change in mutations:
            with self.subTest(change=change):
                fixture = Fixture()
                report = {} if not change else REPORT | change
                with self.assertRaises(ConnectorError):
                    fixture.submit(report=report)
                fixture.submitter.api.assert_not_called()
        fixture = Fixture()
        with self.assertRaises(ConnectorError):
            fixture.submit(plan={})
        fixture.submitter.api.assert_not_called()
        with self.assertRaises(ConnectorError):
            fixture.submitter.submit(TASK, CONTEXT, DELIVERY)
        fixture.submitter.api.assert_not_called()

    def test_new_regression_must_be_absent_and_all_existing_tests_are_rechecked(self):
        for path, response in ((REGRESSION, file_response("Existing test")),
                               (REGRESSION, HTTPFailure(403)),
                               ("tests/test_calc.py", file_response("Changed source")),
                               ("tests/test_calc.py", {"type": "symlink", "target": "calc.py"}),
                               ("tests/test_calc.py", HTTPFailure(404))):
            with self.subTest(path=path, response=response):
                fixture = Fixture()
                fixture.responses[("GET", f"/repos/owner/project/contents/{path}?ref={SHA}")] = response
                with self.assertRaises(ConnectorError):
                    fixture.submit()
                self.assertEqual(fixture.writes(), [])

    def test_context_cannot_allow_existing_tests_scripts_or_sensitive_implementation(self):
        for path in ("tests/test_calc.py", "scripts/run.py", "auth.py", "setup.py", "pkg/security.py", "../escape.py"):
            with self.subTest(path=path):
                fixture = Fixture()
                context = copy.deepcopy(CONTEXT)
                context["files"][path] = "def clamp(value):\n    return value\n"
                context["editable_paths"] = [path, REGRESSION]
                plan = PLAN | {"target_paths": [path, REGRESSION], "source_paths": [path]}
                delivery = copy.deepcopy(DELIVERY)
                delivery["files"][0]["path"] = path
                with self.assertRaises(ConnectorError):
                    fixture.submit(context=context, plan=plan, delivery=delivery)
                fixture.submitter.api.assert_not_called()

    def test_unplanned_new_code_extra_files_and_old_test_edits_are_rejected(self):
        for path in ("new_module.py", "tests/test_calc.py", "docs/readme.md", "../escape.py"):
            with self.subTest(path=path):
                fixture = Fixture()
                delivery = copy.deepcopy(DELIVERY)
                delivery["files"][0]["path"] = path
                with self.assertRaises(ConnectorError):
                    fixture.submit(delivery=delivery)
                fixture.submitter.api.assert_not_called()

    def test_context_identity_commit_or_regression_path_must_match_task(self):
        changes = ({"repo": "other/project"}, {"commit": "main"}, {"kind": "documentation"},
                   {"new_files": ["tests/test_other.py"]}, {"regression_path": "tests/test_other.py"},
                   {"files": {}}, {"editable_paths": "calc.py"})
        for change in changes:
            with self.subTest(change=change):
                fixture = Fixture()
                with self.assertRaises(ConnectorError):
                    fixture.submit(context=CONTEXT | change)
                fixture.submitter.api.assert_not_called()

    def test_code_context_cannot_bypass_checks_by_omitting_task_specialist(self):
        fixture = Fixture()
        with self.assertRaises(ConnectorError):
            fixture.submit(task={"url": TASK["url"], "title": TASK["title"]}, report={})
        fixture.submitter.api.assert_not_called()

    def test_idempotent_existing_pr_is_returned_without_writing_again(self):
        fixture = Fixture()
        lookup = "/repos/owner/project/pulls?state=all&head=me%3Abountyos%2Fissue-7-" + hashlib.sha256(TASK["url"].encode()).hexdigest()[:8]
        fixture.responses[("GET", lookup)] = [{"html_url": "https://github.com/owner/project/pull/9"}]
        self.assertTrue(fixture.submit().endswith("/pull/9"))
        self.assertEqual(fixture.writes(), [])
        self.assertEqual(fixture.submitter.api.call_count, 2)


if __name__ == "__main__":
    unittest.main()
