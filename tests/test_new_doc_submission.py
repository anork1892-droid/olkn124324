"""Submission boundaries for new docs; every GitHub API response is a fixture."""
import base64
import copy
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from bountyos.connectors import ConnectorError, GitHubSubmitter, HTTPFailure


SNAPSHOT = "a" * 40
CURRENT = "b" * 40
NEW_PATH = "docs/recovery.md"
TASK = {"url": "https://github.com/owner/project/issues/7", "title": "Document recovery"}
CONTEXT = {
    "repo": "owner/project", "branch": "main", "commit": SNAPSHOT,
    "files": {"README.md": "# Project\nUse the documented reset option.\n",
              "docs/options.md": "# Options\nReset clears the queue.\n"},
    "new_files": [NEW_PATH],
}
DELIVERY = {"files": [{"path": NEW_PATH, "content": "# Recovery\nUse reset to clear the queue.\n"}],
            "summary": "Document the existing reset option."}


def file_response(text):
    return {"type": "file", "encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


class SubmissionFixture:
    def __init__(self, base=CURRENT):
        self.base = base
        self.responses = {}
        self.submitter = GitHubSubmitter(http=Mock())
        self.submitter.api = Mock(side_effect=self.api)

    def api(self, path, **kwargs):
        key = (kwargs.get("method", "GET"), path)
        if key in self.responses:
            result = self.responses[key]
            if isinstance(result, Exception):
                raise result
            return result
        if path == "/user":
            return {"login": "me"}
        if path.startswith("/repos/owner/project/pulls?"):
            return []
        if path == "/repos/owner/project":
            return {"default_branch": "main"}
        if path == "/repos/owner/project/git/ref/heads/main":
            return {"object": {"sha": self.base}}
        if path == "/repos/owner/project/git/commits/" + SNAPSHOT:
            return {"sha": SNAPSHOT}
        if path.startswith("/repos/owner/project/contents/"):
            url = urlsplit(path)
            file = url.path.removeprefix("/repos/owner/project/contents/")
            revision = parse_qs(url.query)["ref"][0]
            if file == NEW_PATH and revision in {SNAPSHOT, self.base}:
                raise HTTPFailure(404)
            if file in CONTEXT["files"] and revision == self.base:
                return file_response(CONTEXT["files"][file])
        if path == "/repos/owner/project/forks" and key[0] == "POST":
            return {"full_name": "me/project"}
        if path == "/repos/me/project/git/commits/" + self.base:
            return {"tree": {"sha": "upstream-tree"}}
        if path.startswith("/repos/me/project/git/ref/heads/"):
            raise HTTPFailure(404)
        if path == "/repos/me/project/git/trees" and key[0] == "POST":
            return {"sha": "new-tree"}
        if path == "/repos/me/project/git/commits" and key[0] == "POST":
            return {"sha": "new-commit"}
        if path == "/repos/me/project/git/refs" and key[0] == "POST":
            return {"ref": kwargs["payload"]["ref"]}
        if path == "/repos/owner/project/pulls" and key[0] == "POST":
            return {"html_url": "https://github.com/owner/project/pull/9"}
        raise AssertionError("Unexpected API request: " + repr(key))

    def contents(self, path, revision, result):
        self.responses[("GET", f"/repos/owner/project/contents/{path}?ref={revision}")] = result

    def calls(self):
        return self.submitter.api.call_args_list

    def writes(self):
        return [call for call in self.calls() if call.kwargs.get("method") in {"POST", "PUT", "PATCH", "DELETE"}]


class NewDocumentationSubmissionTests(unittest.TestCase):
    def test_new_document_is_checked_at_both_commits_and_added_to_tree_and_pr(self):
        fixture = SubmissionFixture()
        url = fixture.submitter.submit(TASK, CONTEXT, DELIVERY)
        self.assertEqual(url, "https://github.com/owner/project/pull/9")
        paths = [call.args[0] for call in fixture.calls()]
        first_write = next(index for index, call in enumerate(fixture.calls()) if call.kwargs.get("method") == "POST")
        for revision in (SNAPSHOT, CURRENT):
            self.assertLess(paths.index(f"/repos/owner/project/contents/{NEW_PATH}?ref={revision}"), first_write)
        for path in CONTEXT["files"]:
            self.assertLess(paths.index(f"/repos/owner/project/contents/{path}?ref={CURRENT}"), first_write)
        tree = next(call for call in fixture.writes() if call.args[0].endswith("/git/trees"))
        self.assertEqual(tree.kwargs["payload"], {"base_tree": "upstream-tree", "tree": [
            {"path": NEW_PATH, "mode": "100644", "type": "blob", "content": DELIVERY["files"][0]["content"]}]})
        commit = next(call for call in fixture.writes() if call.args[0].endswith("/git/commits"))
        self.assertEqual(commit.kwargs["payload"]["parents"], [CURRENT])
        self.assertIn("No project tests were executed", fixture.writes()[-1].kwargs["payload"]["body"])

    def test_same_snapshot_and_base_checks_absence_once(self):
        fixture = SubmissionFixture(base=SNAPSHOT)
        fixture.submitter.submit(TASK, CONTEXT, DELIVERY)
        paths = [call.args[0] for call in fixture.calls()]
        self.assertEqual(paths.count(f"/repos/owner/project/contents/{NEW_PATH}?ref={SNAPSHOT}"), 1)
        self.assertNotIn("/repos/owner/project/git/commits/" + SNAPSHOT, paths)

    def test_new_path_requires_explicit_permission_and_valid_snapshot_before_writes(self):
        contexts = []
        for update in ({"new_files": []}, {"new_files": NEW_PATH}, {"new_files": ["docs/../escape.md", NEW_PATH]},
                       {"commit": None}, {"commit": "main"}, {"commit": "x" * 40}, {"files": {}},
                       {"new_files": None}, {"files": []}):
            contexts.append(CONTEXT | update)
        for context in contexts:
            with self.subTest(context=context):
                fixture = SubmissionFixture()
                with self.assertRaises(ConnectorError):
                    fixture.submitter.submit(TASK, context, DELIVERY)
                self.assertEqual(fixture.writes(), [])

    def test_missing_or_unverified_snapshot_cannot_turn_a_404_into_absence_proof(self):
        for result in (HTTPFailure(404), {"sha": CURRENT}, None, []):
            with self.subTest(result=result):
                fixture = SubmissionFixture()
                fixture.responses[("GET", "/repos/owner/project/git/commits/" + SNAPSHOT)] = result
                with self.assertRaises(ConnectorError):
                    fixture.submitter.submit(TASK, CONTEXT, DELIVERY)
                self.assertEqual(fixture.writes(), [])
                self.assertFalse(any("/contents/" in call.args[0] for call in fixture.calls()))

    def test_existing_file_directory_or_symlink_cannot_be_replaced_as_new(self):
        for revision in (SNAPSHOT, CURRENT):
            for result in (file_response("Already created"), {"type": "dir"}, {"type": "symlink", "target": "README.md"}, []):
                with self.subTest(revision=revision, result=result):
                    fixture = SubmissionFixture()
                    fixture.contents(NEW_PATH, revision, result)
                    with self.assertRaisesRegex(ConnectorError, "finns redan"):
                        fixture.submitter.submit(TASK, CONTEXT, DELIVERY)
                    self.assertEqual(fixture.writes(), [])

    def test_only_404_counts_as_absent_at_either_revision(self):
        for revision in (SNAPSHOT, CURRENT):
            for failure in (HTTPFailure(403), HTTPFailure(429), HTTPFailure(500), ConnectorError("Network unavailable")):
                with self.subTest(revision=revision, failure=failure):
                    fixture = SubmissionFixture()
                    fixture.contents(NEW_PATH, revision, failure)
                    with self.assertRaises(ConnectorError):
                        fixture.submitter.submit(TASK, CONTEXT, DELIVERY)
                    self.assertEqual(fixture.writes(), [])

    def test_changed_unedited_evidence_stops_new_document_publication(self):
        for path in CONTEXT["files"]:
            with self.subTest(path=path):
                fixture = SubmissionFixture()
                fixture.contents(path, CURRENT, file_response("Changed upstream"))
                with self.assertRaisesRegex(ConnectorError, "Underlaget har ändrats"):
                    fixture.submitter.submit(TASK, CONTEXT, DELIVERY)
                self.assertEqual(fixture.writes(), [])

    def test_deleted_directory_symlink_or_unreadable_evidence_stops_before_writes(self):
        original = CONTEXT["files"]["README.md"]
        for result in (HTTPFailure(404), HTTPFailure(503), [], {"type": "dir"},
                       file_response(original) | {"type": "symlink"},
                       file_response(original) | {"target": "other.md"},
                       file_response(original) | {"submodule_git_url": "https://example.org"},
                       {"type": "file", "content": "!invalid!"}, {"type": "file", "content": None}):
            with self.subTest(result=result):
                fixture = SubmissionFixture()
                fixture.contents("README.md", CURRENT, result)
                with self.assertRaises(ConnectorError):
                    fixture.submitter.submit(TASK, CONTEXT, DELIVERY)
                self.assertEqual(fixture.writes(), [])

    def test_delivery_validation_runs_before_any_api_read_even_if_new_path_list_allows_it(self):
        for path in ("docs/../escape.md", "docs/AGENTS.md", "docs/script.py", "docs/.hidden.md"):
            with self.subTest(path=path):
                fixture = SubmissionFixture()
                delivery = copy.deepcopy(DELIVERY)
                delivery["files"][0]["path"] = path
                with self.assertRaises(ConnectorError):
                    fixture.submitter.submit(TASK, CONTEXT | {"new_files": [path]}, delivery)
                fixture.submitter.api.assert_not_called()

    def test_existing_document_envelope_without_new_fields_still_submits(self):
        fixture = SubmissionFixture()
        existing_delivery = {"files": [{"path": "README.md", "content": "# Updated project\n"}], "summary": "Clarify docs."}
        fixture.submitter.submit(TASK, {"files": CONTEXT["files"]}, existing_delivery)
        reads = [call.args[0] for call in fixture.calls() if "/contents/" in call.args[0]]
        self.assertEqual(reads, [f"/repos/owner/project/contents/README.md?ref={CURRENT}"])
        self.assertTrue(fixture.writes())


if __name__ == "__main__":
    unittest.main()
