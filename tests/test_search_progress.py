import json
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from bountyos.connectors import ConnectorError, GitHub, RateLimited
from bountyos.store import Store


class SearchProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    @staticmethod
    def transport(*, page_count=None, empty=False):
        payload = {"results": [] if empty else [{"url": "/owner/project/issues/7"}]}
        if page_count is not None:
            payload.update(page_count=page_count, result_count=page_count * 10)
        transport = Mock()
        transport.get.return_value = json.dumps({"payload": payload})
        return transport

    @staticmethod
    def requested(transport):
        params = parse_qs(urlsplit(transport.get.call_args.args[0]).query)
        return params["q"][0], int(params["p"][0])

    def test_actual_new_store_and_connector_resume_query_and_page(self):
        transport = self.transport(page_count=25)
        first = GitHub(http=transport, state_store=self.store)
        for _ in range(len(first.SEARCH_QUERIES) + 2):
            first.search()

        reopened = Store(self.tmp.name)
        resumed_transport = self.transport(page_count=25)
        resumed = GitHub(http=resumed_transport, state_store=reopened)
        resumed.search()
        self.assertEqual(self.requested(resumed_transport), (first.SEARCH_QUERIES[2], 2))
        self.assertEqual(resumed.last_search, {
            "query": first.SEARCH_QUERIES[2], "page": 2, "page_count": 25,
            "result_count": 250, "returned": 1, "automatic": True,
        })
        self.assertEqual(reopened.get_internal_state(GitHub.SEARCH_STATE_KEY)["next_query"], first.SEARCH_QUERIES[3])

    def test_explicit_query_never_changes_persisted_automatic_cursor(self):
        transport = self.transport()
        github = GitHub(http=transport, state_store=self.store)
        github.search()
        saved = self.store.get_internal_state(GitHub.SEARCH_STATE_KEY)
        github.search(query="repo:owner/project documentation")
        self.assertEqual(self.requested(transport), ("repo:owner/project documentation", 1))
        self.assertFalse(github.last_search["automatic"])
        self.assertEqual(self.store.get_internal_state(GitHub.SEARCH_STATE_KEY), saved)
        GitHub(http=transport, state_store=Store(self.tmp.name)).search()
        self.assertEqual(self.requested(transport), (github.SEARCH_QUERIES[1], 1))

    def test_failed_or_unreadable_page_retries_same_persisted_cursor_after_restart(self):
        query = GitHub.SEARCH_QUERIES[2]
        self.store.set_internal_state(GitHub.SEARCH_STATE_KEY, {
            "version": 1, "next_query": query, "pages": {query: 17},
        })
        saved = self.store.get_internal_state(GitHub.SEARCH_STATE_KEY)
        failures = (
            RateLimited("HTTP 429"), ConnectorError("Network unavailable"),
            '{"payload":', json.dumps({"payload": {"results": [{"repo": None}]}}),
            json.dumps({"payload": {"results": [], "errors": ["Invalid query"]}}),
        )
        for failure in failures:
            with self.subTest(failure=failure):
                failed_transport = Mock()
                failed_transport.get.side_effect = [failure]
                failed = GitHub(http=failed_transport, state_store=Store(self.tmp.name))
                with self.assertRaises(ConnectorError):
                    failed.search()
                self.assertEqual(self.requested(failed_transport), (query, 17))
                self.assertIsNone(failed.last_search)
                self.assertEqual(self.store.get_internal_state(GitHub.SEARCH_STATE_KEY), saved)

        recovered_transport = self.transport()
        GitHub(http=recovered_transport, state_store=Store(self.tmp.name)).search()
        self.assertEqual(self.requested(recovered_transport), (query, 17))
        self.assertEqual(self.store.get_internal_state(GitHub.SEARCH_STATE_KEY)["pages"][query], 18)

    def test_explicit_end_metadata_resets_without_fetching_an_extra_empty_page(self):
        query = GitHub.SEARCH_QUERIES[0]
        self.store.set_internal_state(GitHub.SEARCH_STATE_KEY, {
            "version": 1, "next_query": query, "pages": {query: 4},
        })
        transport = self.transport(page_count=4)
        GitHub(http=transport, state_store=self.store).search()
        self.assertEqual(self.requested(transport), (query, 4))
        self.assertEqual(self.store.get_internal_state(GitHub.SEARCH_STATE_KEY)["pages"][query], 1)

    def test_short_page_is_not_an_end_when_metadata_declares_more_pages(self):
        query = GitHub.SEARCH_QUERIES[0]
        github = GitHub(http=self.transport(page_count=21), state_store=self.store)
        github.search()
        self.assertEqual(self.store.get_internal_state(GitHub.SEARCH_STATE_KEY)["pages"][query], 2)

    def test_empty_page_resets_even_if_previous_cursor_was_deep(self):
        query = GitHub.SEARCH_QUERIES[0]
        self.store.set_internal_state(GitHub.SEARCH_STATE_KEY, {
            "version": 1, "next_query": query, "pages": {query: 35},
        })
        GitHub(http=self.transport(empty=True), state_store=self.store).search()
        self.assertEqual(self.store.get_internal_state(GitHub.SEARCH_STATE_KEY)["pages"][query], 1)

    def test_pagination_reaches_beyond_ten_and_is_bounded_at_one_hundred(self):
        query = GitHub.SEARCH_QUERIES[0]
        for page, expected in ((10, 11), (99, 100), (100, 1)):
            with self.subTest(page=page):
                self.store.set_internal_state(GitHub.SEARCH_STATE_KEY, {
                    "version": 1, "next_query": query, "pages": {query: page},
                })
                GitHub(http=self.transport(page_count=1000), state_store=self.store).search()
                self.assertEqual(self.store.get_internal_state(GitHub.SEARCH_STATE_KEY)["pages"][query], expected)

    def test_reordered_added_and_removed_queries_migrate_by_query_text(self):
        class EarlierGitHub(GitHub):
            SEARCH_QUERIES = ("old-a", "old-b", "removed")

        class LaterGitHub(GitHub):
            SEARCH_QUERIES = ("new", "old-b", "old-a")

        before = EarlierGitHub(http=self.transport(), state_store=self.store)
        for _ in range(4):
            before.search()
        transport = self.transport()
        later = LaterGitHub(http=transport, state_store=Store(self.tmp.name))
        later.search()
        self.assertEqual(self.requested(transport), ("old-b", 2))
        saved = self.store.get_internal_state(GitHub.SEARCH_STATE_KEY)
        self.assertEqual(saved["pages"], {"new": 1, "old-b": 3, "old-a": 3})
        self.assertEqual(saved["next_query"], "old-a")

    def test_saved_cursor_validation_rejects_wrong_types_and_out_of_range_pages(self):
        queries = GitHub.SEARCH_QUERIES
        for invalid_page in (0, -1, 101, True, 2.0, "2", None, {}, []):
            with self.subTest(page=invalid_page):
                self.store.set_internal_state(GitHub.SEARCH_STATE_KEY, {
                    "version": 1, "next_query": queries[0], "pages": {queries[0]: invalid_page},
                })
                transport = self.transport()
                GitHub(http=transport, state_store=Store(self.tmp.name)).search()
                self.assertEqual(self.requested(transport), (queries[0], 1))
        for malformed in ([], None, "state", {"version": True}, {"version": 2},
                          {"version": 1, "next_query": {}, "pages": []}):
            with self.subTest(saved=malformed):
                self.store.set_internal_state(GitHub.SEARCH_STATE_KEY, malformed)
                transport = self.transport()
                GitHub(http=transport, state_store=Store(self.tmp.name)).search()
                self.assertEqual(self.requested(transport), (queries[0], 1))

    def test_corrupted_internal_json_recovers_without_changing_user_settings(self):
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute("INSERT INTO internal_state(key,value) VALUES (?,?)", (GitHub.SEARCH_STATE_KEY, "{"))
        settings = self.store.get_settings()
        transport = self.transport()
        github = GitHub(http=transport, state_store=Store(self.tmp.name))
        github.search()
        self.assertEqual(self.requested(transport), (github.SEARCH_QUERIES[0], 1))
        self.assertEqual(self.store.get_settings(), settings)
        self.assertNotIn(GitHub.SEARCH_STATE_KEY, settings)

    def test_existing_database_gets_internal_state_storage_on_upgrade(self):
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute("DROP TABLE internal_state")
        reopened = Store(self.tmp.name)
        github = GitHub(http=self.transport(), state_store=reopened)
        github.search()
        self.assertEqual(Store(self.tmp.name).get_internal_state(GitHub.SEARCH_STATE_KEY)["next_query"], github.SEARCH_QUERIES[1])


if __name__ == "__main__":
    unittest.main()
