"""No external reads/writes or model calls: behavior is exercised using fixtures."""
import base64
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from bountyos.connectors import (ConnectorError, GitHub, GitHubSubmitter, HTTP, HTTPFailure, Ollama,
                                 RateLimited, qualify, safe_doc_path, validate_delivery,
                                 validate_local_url)
from bountyos.engine import Engine
from bountyos.store import Store


def issue(**changes):
    value = {"url": "https://github.com/owner/project/issues/7", "repo": "owner/project", "number": 7,
             "title": "Documentation bounty: clarify README examples, 25 USD", "body": "We pay 25 USD after merging the README clarification. Explain the documented options using the existing guide.",
             "state": "OPEN", "authorAssociation": "OWNER", "author": {"login": "owner"}, "assignedActors": {"nodes": []},
             "linkedPullRequests": {"nodes": []}, "closedByPullRequestsReferences": {"nodes": []}, "timelineItems": {"pageInfo": {"hasNextPage": False}, "edges": []}}
    value.update(changes)
    return value


def delivery():
    return {"files": [{"path": "README.md", "content": "# Example\n\nUse the documented option for the example.\n"}], "summary": "Clarify the documented example."}


class QualifierTests(unittest.TestCase):
    def test_open_maintainer_documentation_task_qualifies(self):
        task = qualify(issue())
        self.assertEqual(task["status"], "qualified")
        self.assertEqual((task["reward_amount"], task["reward_currency"]), (25, "USD"))
        self.assertFalse(task["source_facts"]["reward_verified_paid"])

    def test_no_payout_for_proposed_or_unconfirmed_amount(self):
        for change, status, reason in [
            ({"body": "Suggested bounty: 25 USD"}, "skipped", "förslag"),
            ({"authorAssociation": "NONE"}, "blocked", "maintainer"),
            ({"title": "Docs bounty $25", "body": "Pays $25 after merge"}, "blocked", "dollarvalutan"),
            ({"title": "Documentation request", "body": "Please help"}, "skipped", "ersättning"),
        ]:
            with self.subTest(change=change):
                task = qualify(issue(**change))
                self.assertEqual(task["status"], status)
                self.assertIn(reason, task["reason"])
                self.assertFalse(task["source_facts"]["reward_verified_paid"])

    def test_incidental_bounty_discussion_is_not_pending_work(self):
        task = qualify(issue(title="Agent coordination log", body="Discuss documentation and future bounty ideas."))
        self.assertEqual(task["status"], "skipped")
        self.assertIsNone(task["reward_amount"])

    def test_closed_reserved_competing_or_large_scope_is_blocked(self):
        for change in [
            {"state": "CLOSED"}, {"assignedActors": {"nodes": [{"login": "someone"}]}},
            {"labels": {"nodes": [{"name": "claimed"}]}},
            {"linkedPullRequests": {"nodes": [{"state": "OPEN"}]}},
            {"timelineItems": {"pageInfo": {"hasNextPage": True}}},
            {"body": "We pay 25 USD. Comment /attempt first."},
            {"body": "We pay 25 USD. Implement a GPU benchmark and documentation."},
        ]:
            with self.subTest(change=change):
                self.assertEqual(qualify(issue(**change))["status"], "blocked")

    def test_automation_policy_is_not_a_filter(self):
        self.assertEqual(qualify(issue(body="Pays 25 USD after merge. Documentation for README. AI allowed is unspecified."))["status"], "qualified")


class BoundaryTests(unittest.TestCase):
    def test_document_paths_do_not_escape_artifact_scope(self):
        for value in ["../README.md", "/tmp/README.md", "docs/../../evil.md", "docs//foo.md", "docs/AGENTS.md", "docs/.env", ".github/a.md", "README.md/../x", "docs/x.py", "docs/%2e.md", "docs\\x.md", "docs/x.md\x00"]:
            with self.subTest(value=value):
                self.assertFalse(safe_doc_path(value))
        for value in ["README.md", "FAQ.md", "docs/guide.md", "docs/sub/guide.txt"]:
            self.assertTrue(safe_doc_path(value))

    def test_invalid_and_active_content_rejected(self):
        for value in [{"files": [], "summary": "No"}, {"files": [{"path": "docs/a.md", "content": "<script>alert(1)</script>"}], "summary": "a"}, {"files": [{"path": "README.md", "content": "x" * 80_001}], "summary": "a"}]:
            with self.assertRaises(ConnectorError):
                validate_delivery(value)

    def test_local_url_rejects_remote_endpoint_credentials_and_paths(self):
        for url in ["https://api.openai.com", "http://192.168.1.5:11434", "http://localhost.evil.test", "http://user:pass@localhost:11434", "http://localhost:11434/api", "http://localhost:11434?remote=x"]:
            with self.subTest(url=url), self.assertRaises(ConnectorError):
                validate_local_url(url)
        for url in ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"]:
            self.assertEqual(validate_local_url(url), url)

    def test_github_token_cannot_be_sent_to_other_hosts(self):
        with self.assertRaises(ConnectorError):
            HTTP().get("https://github.com", token="fake-token")

    def test_rate_limit_honors_backoff_and_does_not_retry_immediately(self):
        transport = HTTP(clock=lambda: 100, min_interval=0)
        transport.opener = Mock()
        transport.opener.open.side_effect = HTTPError("https://github.com", 429, "limited", {"Retry-After": "120"}, io.BytesIO())
        with self.assertRaises(RateLimited):
            transport.get("https://github.com")
        with self.assertRaises(RateLimited):
            transport.get("https://github.com")
        self.assertEqual(transport.opener.open.call_count, 1)

    def test_remote_ollama_models_rejected_before_generation(self):
        transport = Mock()
        transport.json.side_effect = [{"models": [{"name": "test:7b"}]}, {"remote_host": "cloud.example", "model_info": {"x": 1}}]
        local = Ollama(http=transport, model="test:7b")
        with self.assertRaises(ConnectorError):
            local.generate("system", {})
        self.assertEqual(transport.json.call_count, 2)

    def test_local_ollama_generation_uses_only_loopback(self):
        transport = Mock()
        transport.json.side_effect = [{"models": [{"name": "test:7b"}]}, {"model_info": {"general.architecture": "test"}}, {"response": '{"ok":true}'}]
        local = Ollama(http=transport, model="test:7b", url="http://127.0.0.1:11434")
        self.assertTrue(local.generate("system", {})["ok"])
        self.assertTrue(all(call.args[0].startswith("http://127.0.0.1:11434/") for call in transport.json.call_args_list))

    def test_no_invented_success_claims_and_no_unread_overwrites(self):
        context = {"files": {"README.md": "# Example\n"}}
        bad = delivery()
        bad["summary"] = "All tests passed."
        with self.assertRaises(ConnectorError):
            Engine._validate_claims(bad, context)
        bad = delivery()
        bad["files"][0]["path"] = "docs/unread.md"
        with self.assertRaises(ConnectorError):
            Engine._validate_claims(bad, context)


class GitHubParsingTests(unittest.TestCase):
    @staticmethod
    def requested_queries(transport):
        return [parse_qs(urlsplit(call.args[0]).query)["q"][0]
                for call in transport.get.call_args_list]

    def test_search_reads_embedded_results_and_deduplicates(self):
        row = {"repo": {"repository": {"owner_login": "owner", "name": "project"}}, "number": 7}
        transport = Mock()
        transport.get.return_value = '<script type="application/json">' + json.dumps({"payload": {"results": [row, row]}}) + '</script>'
        self.assertEqual(GitHub(http=transport).search(), ["https://github.com/owner/project/issues/7"])

    def test_search_accepts_github_raw_json_route_response(self):
        row = {"repo": {"repository": {"owner_login": "owner", "name": "project"}}, "number": 7}
        transport = Mock()
        transport.get.return_value = json.dumps({"payload": {"blackbirdSearchRoute": {"results": [row]}}})
        self.assertEqual(GitHub(http=transport).search(), ["https://github.com/owner/project/issues/7"])

    def test_search_ignores_unrelated_and_nested_issue_references(self):
        real = "https://github.com/owner/project/issues/7"
        unrelated = "https://github.com/other/project/issues/8"
        transport = Mock()
        transport.get.return_value = json.dumps({
            "url": unrelated,
            "metadata": {"results": [{"url": unrelated}]},
            "payload": {"results": [{"url": real, "references": [{"url": unrelated}]}]},
        })
        self.assertEqual(GitHub(http=transport).search(), [real])

    def test_search_accepts_only_valid_direct_rows_and_preserves_limit(self):
        transport = Mock()
        transport.get.return_value = json.dumps({"payload": {"results": [
            None, [], "invalid", {"repo": {"repository": []}, "number": 2},
            {"url": "https://example.org/owner/project/issues/1"},
            {"url": "/owner/project/pull/1"},
            {"repo": {"repository": {"owner_login": "owner", "name": "project"}}, "number": True},
            {"url": "/owner/project/issues/7"},
            {"issue_url": "https://github.com/owner/project/issues/8"},
            {"repo": {"repository": {"owner_login": "owner", "name": "project"}}, "number": 9},
        ]}})
        github = GitHub(http=transport)
        self.assertEqual(github.search(limit=2), ["https://github.com/owner/project/issues/7", "https://github.com/owner/project/issues/8"])
        self.assertEqual(github.search(limit=3)[-1], "https://github.com/owner/project/issues/9")

    def test_automatic_search_rotates_one_bounded_query_per_call(self):
        transport = Mock()
        transport.get.return_value = json.dumps({"payload": {"results": []}})
        github = GitHub(http=transport)
        for _ in range(len(github.SEARCH_QUERIES) + 1):
            self.assertEqual(github.search(), [])
        expected = [*github.SEARCH_QUERIES, github.SEARCH_QUERIES[0]]
        self.assertEqual(self.requested_queries(transport), expected)
        self.assertEqual(transport.get.call_count, len(expected))
        for query in expected:
            for qualifier in ("is:issue", "is:open", "no:assignee", "in:title"):
                self.assertIn(qualifier, query.split())

    def test_explicit_search_does_not_change_automatic_rotation(self):
        transport = Mock()
        transport.get.return_value = json.dumps({"payload": {"results": []}})
        github = GitHub(http=transport)
        github.search()
        github.search(query="repo:owner/project documentation")
        github.search()
        self.assertEqual(self.requested_queries(transport), [
            github.SEARCH_QUERIES[0], "repo:owner/project documentation", github.SEARCH_QUERIES[1],
        ])

    def test_search_failures_retry_same_query_without_automatic_extra_request(self):
        failures = [
            RateLimited("HTTP 429"),
            json.dumps({"error": "unreadable search"}),
            json.dumps({"payload": {"results": [{"repo": None}]}}),
            json.dumps({"payload": {"results": [], "errors": ["invalid query"]}}),
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                transport = Mock()
                transport.get.side_effect = [failure, json.dumps({"payload": {"results": []}})]
                github = GitHub(http=transport)
                with self.assertRaises(ConnectorError):
                    github.search()
                self.assertEqual(transport.get.call_count, 1)
                github.search()
                self.assertEqual(self.requested_queries(transport), [github.SEARCH_QUERIES[0]] * 2)

    def test_unknown_github_payload_is_not_reported_as_zero_results(self):
        transport = Mock()
        transport.get.return_value = json.dumps({"error": "authentication required"})
        with self.assertRaises(ConnectorError):
            GitHub(http=transport).search()

    def test_owner_is_inferred_only_when_author_matches_repository_owner(self):
        raw = issue(authorAssociation=None)
        transport = Mock()
        transport.get.return_value = '<script type="application/json">' + json.dumps({"issue": raw}) + '</script>'
        self.assertEqual(GitHub(http=transport).issue(raw["url"])["authorAssociation"], "OWNER")
        raw["author"] = {"login": "outsider"}
        transport.get.return_value = '<script type="application/json">' + json.dumps({"issue": raw}) + '</script>'
        self.assertEqual(GitHub(http=transport).issue(raw["url"])["authorAssociation"], "UNKNOWN")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.store.update_settings({"running": True})
        self.github = Mock()
        self.github.search.return_value = [issue()["url"]]
        self.github.issue.return_value = issue()
        self.context = {"repo": "owner/project", "branch": "main", "files": {"README.md": "# Example\n"}}
        self.github.repository_context.return_value = self.context
        self.model = Mock()
        self.model.model = "test:7b"
        self.model.generate.side_effect = [delivery(), {"accepted": True, "findings": []}]
        self.submitter = Mock()
        self.submitter.configured = True
        self.submitter.submit.return_value = "https://github.com/owner/project/pull/9"
        self.engine = Engine(self.store, self.github, self.model, self.submitter)

    def tearDown(self):
        self.engine.stop()
        self.temp.cleanup()

    def task(self):
        task_id = self.store.upsert_task(qualify(issue()))
        return self.store.claim_task("worker-1")

    def test_scan_deduplicates_and_never_records_advertised_rewards_as_income(self):
        for _ in range(2):
            self.engine._scan_lock.acquire()
            self.engine._scan()
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertEqual(self.store.get_state()["metrics"]["earned_sek"], 0)

    def test_only_one_scan_can_be_running(self):
        self.engine._scan_lock.acquire()
        try:
            self.assertEqual(self.engine.scan_now()["status"], "already_running")
            self.github.search.assert_not_called()
        finally:
            self.engine._scan_lock.release()

    def test_paused_start_has_no_network_requests(self):
        self.store.update_settings({"running": False})
        self.engine.start()
        self.engine.stop()
        self.github.search.assert_not_called()
        self.model.generate.assert_not_called()

    def test_real_pipeline_writes_reviewed_artifact_without_submitting_when_disabled(self):
        task = self.task()
        self.engine._work("worker-1", task)
        updated = self.store.get_task(task["id"])
        self.assertEqual(updated["status"], "ready")
        self.assertTrue(Path(updated["artifact_path"]).is_file())
        self.assertTrue((Path(updated["artifact_path"]).parent / "files/README.md").is_file())
        self.assertEqual(self.model.generate.call_count, 2)
        self.submitter.submit.assert_not_called()
        self.assertEqual(self.store.get_state()["metrics"]["earned_sek"], 0)

    def test_auto_submit_uses_reviewed_work_when_explicitly_enabled(self):
        self.store.update_settings({"running": True, "auto_submit": True})
        task = self.task()
        self.engine._work("worker-1", task)
        self.assertEqual(self.store.get_task(task["id"])["status"], "submitted")
        self.submitter.submit.assert_called_once()
        self.assertEqual(self.store.get_state()["metrics"]["earned_sek"], 0)

    def test_pause_prevents_publication_after_work_has_finished(self):
        self.store.update_settings({"running": False, "auto_submit": True})
        task = self.task()
        self.engine._work("worker-1", task)
        self.submitter.submit.assert_not_called()

    def test_review_rejection_keeps_artifacts_and_blocks_submission(self):
        self.store.update_settings({"running": True, "auto_submit": True})
        self.model.generate.side_effect = [delivery(), {"accepted": False, "findings": ["Unverified assertion"]}]
        task = self.task()
        self.engine._work("worker-1", task)
        updated = self.store.get_task(task["id"])
        self.assertEqual(updated["status"], "blocked")
        self.assertTrue(Path(updated["artifact_path"]).exists())
        self.submitter.submit.assert_not_called()

    def test_unknown_submission_outcome_is_not_retried(self):
        self.store.update_settings({"running": True, "auto_submit": True})
        self.submitter.submit.side_effect = ConnectorError("HTTP 503")
        task = self.task()
        self.engine._work("worker-1", task)
        updated = self.store.get_task(task["id"])
        self.assertEqual(updated["status"], "blocked")
        self.engine._submit_ready(updated)
        self.assertEqual(self.submitter.submit.call_count, 1)

    def test_scout_never_reactivates_failed_review_or_uncertain_publication(self):
        task = self.task()
        self.store.update_task(task["id"], status="blocked", reason="Unknown publication outcome")
        self.engine._scan_lock.acquire()
        self.engine._scan()
        self.assertEqual(self.store.get_task(task["id"])["status"], "blocked")
        self.github.issue.assert_not_called()

    def test_recheck_stops_claimed_task_before_work(self):
        task = self.task()
        self.github.issue.return_value = issue(assignedActors={"nodes": [{"login": "someone"}]})
        self.engine._work("worker-1", task)
        self.assertEqual(self.store.get_task(task["id"])["status"], "blocked")
        self.model.generate.assert_not_called()

    def test_interrupted_tasks_recover_without_duplicating_publication(self):
        task = self.task()
        self.store.update_task(task["id"], status="submitting")
        self.engine.start()
        self.engine.stop()
        self.assertEqual(self.store.get_task(task["id"])["status"], "blocked")
        self.submitter.submit.assert_not_called()

    def test_autoscaling_requires_received_payments_not_bounty_amounts(self):
        settings = {**self.store.get_settings(), "max_agents": 10, "autoscale": True}
        self.task()
        self.assertEqual(self.engine._worker_limit(settings), 1)

    def test_merged_pr_is_waiting_for_payment_not_income(self):
        task = self.task()
        self.store.update_task(task["id"], status="submitted", submission_url="https://github.com/owner/project/pull/9")
        self.github.http.get.return_value = '<span class="State State--merged">Merged</span>'
        self.engine._poll_submissions()
        self.assertEqual(self.store.get_task(task["id"])["status"], "awaiting_payment")
        self.assertEqual(self.store.get_state()["metrics"]["earned_sek"], 0)


class SubmitterTests(unittest.TestCase):
    def test_existing_pr_is_returned_without_any_write(self):
        submitter = GitHubSubmitter()
        submitter.api = Mock(side_effect=[{"login": "me"}, [{"html_url": "https://github.com/owner/project/pull/9"}]])
        url = submitter.submit(qualify(issue()), {"files": {}}, delivery())
        self.assertTrue(url.endswith("/pull/9"))
        self.assertTrue(all("method" not in call.kwargs for call in submitter.api.call_args_list))

    def test_new_delivery_compares_original_at_pinned_sha_and_commits_once(self):
        submitter = GitHubSubmitter()
        original = "# Example\n"
        submitter.api = Mock(side_effect=[{"login": "me"}, [], {"default_branch": "main"}, {"object": {"sha": "abc123"}},
            {"type": "file", "content": base64.b64encode(original.encode()).decode()}, {"full_name": "me/project"},
            {"tree": {"sha": "tree0"}}, HTTPFailure(404), {"sha": "tree1"}, {"sha": "commit1"}, {"ref": "refs/heads/new"}, {"html_url": "https://github.com/owner/project/pull/9"}])
        submitter.submit(qualify(issue()), {"files": {"README.md": original}}, delivery())
        calls = submitter.api.call_args_list
        self.assertIn("ref=abc123", calls[4].args[0])
        self.assertEqual(sum(call.args[0].endswith("/git/commits") and call.kwargs.get("method") == "POST" for call in calls), 1)
        self.assertIn("No project tests were executed", calls[-1].kwargs["payload"]["body"])

    @patch("bountyos.connectors.time.sleep")
    def test_fork_is_polled_read_only_until_ready(self, sleep):
        submitter = GitHubSubmitter()
        original = "# Example\n"
        submitter.api = Mock(side_effect=[{"login": "me"}, [], {"default_branch": "main"}, {"object": {"sha": "abc123"}},
            {"type": "file", "content": base64.b64encode(original.encode()).decode()}, {"full_name": "me/project"},
            HTTPFailure(404), {"tree": {"sha": "tree0"}}, HTTPFailure(404), {"sha": "tree1"}, {"sha": "commit1"}, {"ref": "refs/heads/new"}, {"html_url": "https://github.com/owner/project/pull/9"}])
        submitter.submit(qualify(issue()), {"files": {"README.md": original}}, delivery())
        calls = submitter.api.call_args_list
        self.assertEqual(sum(call.args[0].endswith("/forks") for call in calls), 1)
        self.assertEqual(sum(call.args[0].endswith("/git/commits/abc123") for call in calls), 2)
        sleep.assert_called_once_with(1)

    def test_interrupted_matching_branch_is_reused_without_new_commit(self):
        submitter = GitHubSubmitter()
        original = "# Example\n"
        submitter.api = Mock(side_effect=[{"login": "me"}, [], {"default_branch": "main"}, {"object": {"sha": "abc123"}},
            {"type": "file", "content": base64.b64encode(original.encode()).decode()}, {"full_name": "me/project"},
            {"tree": {"sha": "tree0"}}, {"object": {"sha": "priorcommit"}}, {"sha": "tree1"},
            {"tree": {"sha": "tree1"}, "parents": [{"sha": "abc123"}]}, {"html_url": "https://github.com/owner/project/pull/9"}])
        submitter.submit(qualify(issue()), {"files": {"README.md": original}}, delivery())
        self.assertFalse(any(call.args[0].endswith(("/git/commits", "/git/refs")) and call.kwargs.get("method") == "POST" for call in submitter.api.call_args_list))

    def test_pause_guard_stops_each_external_write(self):
        submitter = GitHubSubmitter(http=Mock())
        submitter._publish_guard = lambda: False
        with self.assertRaises(ConnectorError):
            submitter.api("/repos/owner/project/forks", method="POST", payload={})
        submitter.http.json.assert_not_called()

    def test_unread_existing_path_is_blocked_before_writes(self):
        submitter = GitHubSubmitter()
        submitter.api = Mock(side_effect=[{"login": "me"}, [], {"default_branch": "main"}, {"object": {"sha": "abc123"}}])
        with self.assertRaises(ConnectorError):
            submitter.submit(qualify(issue()), {"files": {"docs/other.md": "old"}}, delivery())
        self.assertTrue(all("method" not in call.kwargs for call in submitter.api.call_args_list))


if __name__ == "__main__":
    unittest.main()
