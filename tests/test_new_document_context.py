"""New documents require an explicit target, immutable evidence and a real 404."""
import json
import unittest
from unittest.mock import Mock

from bountyos.connectors import (
    ConnectorError, GitHub, HTTPFailure, RateLimited, repository_commit,
    requested_document_paths,
)


class NewDocumentContextTests(unittest.TestCase):
    sha = "a" * 40
    repo = "owner/project"

    def context(self, *, body="Create `docs/recovery.md`.", title="Documentation bounty", files=None, sha=True, tree=None):
        files = files if files is not None else {"README.md": "# Existing project\nReal documented behavior."}
        ref_info = {"name": "main", "refType": "branch"}
        if sha:
            ref_info["currentOid"] = self.sha
        root = {"payload": {"repo": {"defaultBranch": "main"}, "codeViewRepoRoute": {
            "path": "/", "refInfo": ref_info,
            "tree": {"items": [{"path": path} for path in (tree or [])]},
        }}}
        self.transport = Mock()

        def get(url, **kwargs):
            if url == f"https://github.com/{self.repo}":
                return json.dumps(root)
            ref = self.sha if sha else "main"
            if url == f"https://github.com/{self.repo}/tree/{ref}/docs":
                raise HTTPFailure(404)
            prefix = f"https://raw.githubusercontent.com/{self.repo}/{ref}/"
            if not url.startswith(prefix):
                raise AssertionError("Unexpected or unpinned request: " + url)
            result = files.get(url[len(prefix):], HTTPFailure(404))
            if isinstance(result, Exception):
                raise result
            return result

        self.transport.get.side_effect = get
        return GitHub(http=self.transport).repository_context({"repo": self.repo, "title": title, "body": body})

    def test_explicit_missing_file_is_allowed_at_same_pinned_commit_as_evidence(self):
        context = self.context()
        self.assertEqual(context["new_files"], ["docs/recovery.md"])
        self.assertEqual(context["commit"], self.sha)
        self.assertEqual(context["branch"], "main")
        self.assertEqual(set(context["files"]), {"README.md"})
        raw = [call.args[0] for call in self.transport.get.call_args_list if "raw.githubusercontent.com" in call.args[0]]
        self.assertTrue(all(f"/{self.sha}/" in url for url in raw))
        self.assertTrue(raw[0].endswith("/docs/recovery.md"))

    def test_existing_target_stays_in_evidence_not_new_files(self):
        context = self.context(files={"docs/recovery.md": "# Existing recovery guide"})
        self.assertEqual(context["new_files"], [])
        self.assertEqual(context["files"], {"docs/recovery.md": "# Existing recovery guide"})

    def test_absence_without_commit_is_not_permission_to_create(self):
        context = self.context(sha=False)
        self.assertEqual(context["new_files"], [])
        self.assertNotIn("commit", context)
        self.assertIn("Git-version", context["new_files_unavailable_reason"])
        self.assertIn("README.md", context["files"])

    def test_missing_context_cannot_be_replaced_with_empty_new_file(self):
        with self.assertRaisesRegex(ConnectorError, "Inga stödda dokument"):
            self.context(files={})

    def test_only_404_confirms_new_target_absence(self):
        for failure in (HTTPFailure(403), HTTPFailure(500), RateLimited("HTTP 429"), ConnectorError("TLS failure")):
            with self.subTest(failure=failure), self.assertRaisesRegex(ConnectorError, "docs/recovery.md"):
                self.context(files={"docs/recovery.md": failure, "README.md": "# Real documentation"})

    def test_ordinary_and_negated_mentions_do_not_authorize_new_documents(self):
        for body in (
            "See [docs/recovery.md](https://github.com/owner/project/blob/main/docs/recovery.md).",
            "Add a link to `docs/recovery.md`.",
            "Add a section to `docs/recovery.md`.",
            "Write about `docs/recovery.md`.",
            "Do not create `docs/recovery.md`.",
            "Never add `docs/recovery.md`.",
            "Use README.md without creating `docs/recovery.md`.",
        ):
            with self.subTest(body=body):
                self.assertEqual(self.context(body=body)["new_files"], [])

    def test_new_file_request_can_be_in_title(self):
        context = self.context(title="Docs: add a new guide at `docs/recovery.md`", body="Pay $25.")
        self.assertEqual(context["new_files"], ["docs/recovery.md"])

    def test_unsafe_and_policy_paths_are_neither_fetched_nor_approved(self):
        for path in ("../docs/recovery.md", "/docs/recovery.md", "docs/../recovery.md", "docs/AGENTS.md", "docs/SKILL.md",
                     "docs/.hidden/recovery.md", "docs/credentials.md", "docs%2frecovery.md", r"docs\recovery.md",
                     "docs/recovery.md/../code", "AGENTS.md"):
            with self.subTest(path=path):
                context = self.context(body=f"Create `{path}`.")
                self.assertEqual(context["new_files"], [])
                raw = [call.args[0] for call in self.transport.get.call_args_list if "raw.githubusercontent.com" in call.args[0]]
                self.assertTrue(all(url.endswith(("/README.md", "/FAQ.md")) for url in raw), raw)

    def test_issue_paths_take_priority_and_raw_reads_remain_bounded(self):
        context = self.context(
            body="Update `docs/requested.md`. Create `docs/new.md`.",
            files={"README.md": "# Project", "docs/requested.md": "# Target"},
            tree=[f"docs/other-{index}.md" for index in range(30)],
        )
        raw = [call.args[0] for call in self.transport.get.call_args_list if "raw.githubusercontent.com" in call.args[0]]
        self.assertEqual(len(raw), 10)
        self.assertTrue(raw[0].endswith("/docs/requested.md"))
        self.assertTrue(raw[1].endswith("/docs/new.md"))
        self.assertIn("docs/requested.md", context["files"])
        self.assertEqual(context["new_files"], ["docs/new.md"])


class DocumentRequestParsingTests(unittest.TestCase):
    def test_direct_requests_allow_create_add_and_write_new(self):
        for command in ("Create", "Add", "Write a new file", "Create a new documentation file named"):
            with self.subTest(command=command):
                self.assertEqual(requested_document_paths({"body": f"{command} `docs/new.md`."}), (["docs/new.md"], ["docs/new.md"]))

    def test_sentence_punctuation_does_not_hide_an_unquoted_target(self):
        self.assertEqual(requested_document_paths({"body": "Create docs/new.md. Use existing README.md as evidence."}),
                         (["docs/new.md", "README.md"], ["docs/new.md"]))
        self.assertEqual(requested_document_paths({"body": "Create docs/new.md.png."}), ([], []))

    def test_commit_metadata_is_structured_and_must_be_unambiguous(self):
        sha, other = "a" * 40, "b" * 40
        self.assertEqual(repository_commit([{"payload": {"currentCommit": {"oid": sha}}}], "main"), sha)
        for root in (
            {"payload": {"currentCommit": {"oid": "main"}}},
            {"payload": {"currentOid": sha, "currentCommit": {"oid": other}}},
            {"payload": {"currentOid": sha, "refInfo": {"name": "different-branch"}}},
            {"payload": {"readme": {"currentOid": sha}}},
            {"payload": {"codeViewRepoRoute": {"path": "docs/file.md", "currentOid": sha}}},
        ):
            with self.subTest(root=root):
                self.assertIsNone(repository_commit([root], "main"))


if __name__ == "__main__":
    unittest.main()
