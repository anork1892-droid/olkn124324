import copy
from datetime import date, timedelta
import json
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from bountyos.bountyhub import BountyHub, CATALOG_URL, RATE_URL, Rates, bountyhub_links, funding_evidence
from bountyos.connectors import ConnectorError, GitHub, HTTPFailure
from bountyos.store import Store


LISTING = "https://www.bountyhub.dev/bounty/view/e4fafc93-d052-467e-bfa2-ec3da3ab7884"
ISSUE = "https://github.com/owner/project/issues/5"


def comment(body, *, author="bountyhub-bot", role="NONE", number=10, day=1):
    return {
        "__typename": "IssueComment", "url": ISSUE + f"#issuecomment-{number}",
        "author": {"login": author}, "authorAssociation": role, "body": body,
        "createdAt": f"2026-10-{day:02d}T12:00:00Z", "isHidden": False, "isMinimized": False,
    }


def announcement(amount="USD 50", *, update=False, contribution="USD 5"):
    # Public bot templates observed on go-github #5 and microG #2843.
    text = (f"**A contribution of** {contribution} **has been made by** [donor](https://github.com/donor) "
            f"**on BountyHub** for this issue.\n**The total amount of the bounty is now** {amount}"
            if update else f"**A bounty of** {amount} **has been created by** [donor](https://github.com/donor) **on BountyHub** for this issue.")
    return text + f"\n**[Claim this bounty by submitting a pull request that solves this issue!]({LISTING})**"


def issue(amount="USD 50", *, author_role="NONE"):
    return {
        "url": ISSUE, "repo": "owner/project", "number": 5,
        "title": "Docs: clarify README examples", "body": "Clarify the existing examples in README.md.",
        "state": "OPEN", "author": {"login": "contributor"}, "authorAssociation": author_role,
        "assignedActors": {"nodes": []}, "labels": {"nodes": []},
        "linkedPullRequests": {"nodes": []}, "closedByPullRequestsReferences": {"nodes": []},
        "timelineItems": {"edges": [{"node": comment(announcement(amount))}],
                          "pageInfo": {"hasNextPage": False, "hasPreviousPage": False}},
        "backTimelineItems": {"edges": [], "pageInfo": {"hasNextPage": False, "hasPreviousPage": False}},
    }


class RatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.today = date(2026, 10, 18)
        self.payload = {"base": "EUR", "date": "2026-10-16", "amount": 1, "rates": {"SEK": 11.0, "USD": 1.1}}
        self.fetch = Mock(return_value=self.payload)
        self.rates = Rates(state_store=self.store, fetch=self.fetch, today=lambda: self.today)

    def test_explicit_rates_are_dated_selection_estimates_never_income(self):
        usd = self.rates.estimate(125, "USD")
        eur = self.rates.estimate(100, "EUR")
        self.assertEqual(usd["amount_sek"], 1250)
        self.assertEqual(eur["amount_sek"], 1100)
        self.assertEqual(usd["rate_date"], "2026-10-16")
        self.assertEqual(usd["rate_source"], RATE_URL)
        self.assertTrue(usd["selection_only"])
        self.assertFalse(usd["assumed_currency"])
        self.fetch.assert_called_once_with(RATE_URL)

    def test_sek_and_unresolved_dollars_do_not_make_network_requests(self):
        sek = self.rates.estimate(900, "SEK")
        unknown = self.rates.estimate(100, "$", assume_usd=False)
        self.assertEqual(sek["amount_sek"], 900)
        self.assertIsNone(sek["rate_date"])
        self.assertEqual(unknown["status"], "unresolved")
        self.assertIn("ospecificerad", unknown["reason"])
        self.fetch.assert_not_called()

    def test_user_authorized_dollar_assumption_is_explicit_and_original_preserved(self):
        estimate = self.rates.estimate(125, "$")
        self.assertEqual(estimate["amount_sek"], 1250)
        self.assertEqual(estimate["currency"], "$")
        self.assertEqual(estimate["conversion_currency"], "USD")
        self.assertTrue(estimate["assumed_currency"])
        self.assertIn("originalvalutan är ospecificerad", estimate["reason"])

    def test_fresh_cache_survives_restart_and_is_rechecked_next_day(self):
        self.rates.estimate(50, "USD")
        restarted = Rates(state_store=self.store, fetch=self.fetch, today=lambda: self.today)
        self.assertEqual(restarted.estimate(50, "USD")["amount_sek"], 500)
        self.assertEqual(self.fetch.call_count, 1)
        self.today += timedelta(days=1)
        self.assertEqual(restarted.estimate(50, "USD")["amount_sek"], 500)
        self.assertEqual(self.fetch.call_count, 2)

    def test_cache_expiry_and_future_dates_cannot_produce_estimates(self):
        for day in ("2026-10-10", "2026-10-19", "bad"):
            with self.subTest(day=day):
                rates = Rates(fetch=lambda _: {**self.payload, "date": day}, today=lambda: self.today)
                self.assertEqual(rates.estimate(100, "EUR")["status"], "unresolved")

    def test_invalid_rate_shapes_rejected(self):
        for payload in (
            None, {**self.payload, "base": "USD"}, {**self.payload, "amount": 10},
            {**self.payload, "rates": {"SEK": 11}}, {**self.payload, "rates": {"SEK": 0, "USD": 1}},
            {**self.payload, "rates": {"SEK": float("inf"), "USD": 1}},
            {**self.payload, "rates": {"SEK": 11, "USD": True}},
        ):
            with self.subTest(payload=payload):
                rates = Rates(fetch=lambda _: payload, today=lambda: self.today)
                self.assertEqual(rates.estimate(100, "USD")["status"], "unresolved")

    def test_transient_failure_uses_only_still_valid_cache(self):
        self.rates.estimate(50, "USD")
        self.fetch.side_effect = ConnectorError("Unavailable")
        self.today = date(2026, 10, 23)
        self.assertEqual(self.rates.estimate(50, "USD")["amount_sek"], 500)
        self.today = date(2026, 10, 24)
        self.assertEqual(self.rates.estimate(50, "USD")["status"], "unresolved")

    def test_failed_rate_source_has_bounded_retry_without_stopping_discovery(self):
        self.fetch.side_effect = ConnectorError("Offline")
        for _ in range(3):
            self.assertEqual(self.rates.estimate(50, "USD")["status"], "unresolved")
        self.assertEqual(self.fetch.call_count, 1)

    def test_invalid_amounts_never_call_rate_source(self):
        for amount in (0, -1, None, True, float("nan"), float("inf"), "100"):
            with self.subTest(amount=amount):
                self.assertEqual(self.rates.estimate(amount, "USD")["status"], "unresolved")
        self.fetch.assert_not_called()


class BountyHubQualificationTests(unittest.TestCase):
    def setUp(self):
        self.rates = Rates(fetch=lambda _: {"base": "EUR", "date": "2026-10-16", "rates": {"SEK": 11, "USD": 1.1}},
                           today=lambda: date(2026, 10, 18))
        self.connector = BountyHub(http=Mock(), rates=self.rates)

    def test_third_party_funding_does_not_fabricate_project_role_or_paid_income(self):
        task = self.connector.qualify(issue())
        self.assertEqual(task["status"], "qualified")
        self.assertEqual(task["source"], "bountyhub")
        self.assertEqual(task["specialist"], "documentation")
        self.assertEqual(task["bountyhub_url"], LISTING)
        self.assertEqual(task["source_facts"]["author_association"], "NONE")
        self.assertFalse(task["source_facts"]["reward_verified_paid"])
        self.assertFalse(task["source_facts"]["bountyhub_current_funding_verified"])
        self.assertEqual(task["bountyhub_evidence"][0]["source_url"], ISSUE + "#issuecomment-10")
        self.assertEqual(task["reward_currency"], "USD")

    def test_latest_total_wins_over_individual_contribution(self):
        data = issue()
        data["timelineItems"]["edges"].append({"node": comment(announcement("USD 125", update=True, contribution="USD 5"), day=2, number=20)})
        task = self.connector.qualify(data)
        self.assertEqual(task["reward_amount"], 125)
        self.assertEqual(task["price_estimate"]["amount_sek"], 1250)
        self.assertEqual(task["status"], "qualified")

    def test_soft_preference_never_filters_larger_or_unknown_fx_rewards(self):
        large = self.connector.qualify(issue("USD 250"), max_sek=1000)
        self.assertEqual(large["status"], "qualified")
        self.assertFalse(large["price_estimate"]["within_preference"])
        unknown = self.connector.qualify(issue("$125"), assume_usd=False)
        self.assertEqual(unknown["status"], "qualified")
        self.assertIsNone(unknown["price_estimate"]["within_preference"])
        self.assertEqual(unknown["reward_currency"], "$")
        unavailable = BountyHub(http=Mock(), rates=Rates(fetch=Mock(side_effect=ConnectorError("Offline"))))
        self.assertEqual(unavailable.qualify(issue())["status"], "qualified")

    def test_dollars_remain_original_currency_with_user_authorized_estimate(self):
        task = self.connector.qualify(issue("$125"))
        self.assertEqual(task["status"], "qualified")
        self.assertEqual(task["reward_currency"], "$")
        self.assertTrue(task["price_estimate"]["assumed_currency"])
        self.assertFalse(task["source_facts"]["reward_currency_explicit"])

    def test_explicit_non_us_dollars_are_never_estimated_as_usd(self):
        for amount in ("CAD $50", "$50 CAD", "AUD $50"):
            with self.subTest(amount=amount):
                task = self.connector.qualify(issue(amount))
                self.assertEqual(task["status"], "qualified")
                self.assertIn(task["reward_currency"], {"CAD", "AUD"})
                self.assertFalse(task["price_estimate"]["assumed_currency"])
                self.assertEqual(task["price_estimate"]["status"], "unresolved")

    def test_direct_author_link_requires_actual_project_role_without_bot(self):
        data = issue()
        data["timelineItems"]["edges"] = []
        data["body"] += "\nBounty: 500 SEK " + LISTING
        self.assertEqual(self.connector.qualify(data)["status"], "blocked")
        data["authorAssociation"] = "OWNER"
        task = self.connector.qualify(data)
        self.assertEqual(task["status"], "qualified")
        self.assertEqual(task["bountyhub_evidence"][0]["kind"], "issue_author")

    def test_unrelated_domain_and_arbitrary_comment_are_not_funding_evidence(self):
        for text in (announcement().replace("bountyhub.dev", "bountyhub.org"), announcement().replace("bountyhub.dev", "bountyhub.dev.evil.test")):
            data = issue()
            data["timelineItems"]["edges"] = [{"node": comment(text)}]
            self.assertEqual(self.connector.qualify(data)["status"], "skipped")
        for author in ("someone", "bountyhub-bot-fake"):
            data = issue()
            data["timelineItems"]["edges"] = [{"node": comment(announcement(), author=author)}]
            self.assertEqual(self.connector.qualify(data)["status"], "skipped")

    def test_cross_referenced_hidden_and_quoted_announcements_are_ignored(self):
        for change in ({"url": "https://github.com/other/project/issues/5#issuecomment-10"},
                       {"isHidden": True}, {"isMinimized": True}, {"__typename": "CrossReferencedEvent"},
                       {"body": "\n".join("> " + line for line in announcement().splitlines())}):
            data = issue()
            data["timelineItems"]["edges"][0]["node"].update(change)
            with self.subTest(change=change):
                self.assertEqual(self.connector.qualify(data)["status"], "skipped")

    def test_outsider_currency_in_comment_does_not_relabel_bot_dollars(self):
        data = issue("$10.00")
        data["timelineItems"]["edges"].append({"node": comment("My fix costs USD 200. " + LISTING, author="outsider", number=20, day=2)})
        task = self.connector.qualify(data)
        self.assertEqual(task["reward_amount"], 10)
        self.assertEqual(task["reward_currency"], "$")

    def test_ambiguous_listing_identity_and_new_unreadable_bot_total_block(self):
        data = issue()
        new = announcement("USD 60").replace("e4fafc93", "12345678")
        data["timelineItems"]["edges"].append({"node": comment(new, day=2, number=20)})
        self.assertIn("Flera", self.connector.qualify(data)["reason"])
        data["timelineItems"]["edges"][-1]["node"]["body"] = "A contribution of USD 10 on BountyHub. " + LISTING
        self.assertIn("totalbelopp", self.connector.qualify(data)["reason"])

    def test_removed_test_or_refused_bounty_from_maintainer_blocks(self):
        for text in ("I removed BountyHub message. It was test only.", "The bounty was withdrawn.", "We do not accept bounties.", "This bounty has been closed.", "The bounty was paid out."):
            data = issue()
            data["timelineItems"]["edges"].append({"node": comment(text, author="maintainer", role="MEMBER", number=20, day=2)})
            task = self.connector.qualify(data)
            with self.subTest(text=text):
                self.assertEqual(task["status"], "blocked")
                self.assertEqual(task["bountyhub_withdrawal_url"], ISSUE + "#issuecomment-20")

    def test_outsider_or_quoted_withdrawal_is_not_maintainer_rejection(self):
        for role, body in (("NONE", "The bounty was withdrawn."), ("MEMBER", "> The bounty was withdrawn.\nThat is incorrect."),
                           ("MEMBER", "The bounty will be paid when the pull request is accepted.")):
            data = issue()
            data["timelineItems"]["edges"].append({"node": comment(body, author="person", role=role, number=20, day=2)})
            self.assertEqual(self.connector.qualify(data)["status"], "qualified")

    def test_availability_and_mirror_guards_are_preserved(self):
        changes = (
            {"state": "CLOSED"}, {"assignedActors": {"nodes": [{"login": "worker"}]}},
            {"labels": {"nodes": [{"name": "in progress"}]}},
            {"body": "You must claim the issue before working."},
            {"linkedPullRequests": {"nodes": [{"state": "OPEN"}]}},
            {"closedByPullRequestsReferences": {"nodes": [{"state": "MERGED"}]}},
        )
        for change in changes:
            data = issue()
            data.update(change)
            with self.subTest(change=change):
                self.assertEqual(self.connector.qualify(data)["status"], "blocked")
        data = issue()
        original = "https://github.com/original/project/issues/10"
        data["body"] = "## Source URL\n" + original
        task = self.connector.qualify(data)
        self.assertEqual(task["status"], "skipped")
        self.assertEqual(task["original_issue_url"], original)

    def test_both_timeline_fragments_must_be_complete(self):
        for timeline in ("timelineItems", "backTimelineItems"):
            for flag in ("hasNextPage", "hasPreviousPage"):
                data = issue()
                data[timeline]["pageInfo"][flag] = True
                self.assertIn("ofullständigt", self.connector.qualify(data)["reason"])

    def test_python_code_routes_to_specialist_but_broad_or_other_languages_do_not(self):
        data = issue()
        data.update(title="Fix Python parser edge case", body="Fix parse_number in parser.py and add regression tests.")
        task = self.connector.qualify(data)
        self.assertEqual(task["status"], "qualified")
        self.assertEqual(task["specialist"], "code")
        data.update(title="Fix Go parser", body="Fix parser.go pagination.")
        self.assertEqual(self.connector.qualify(data)["status"], "skipped")
        data.update(title="Rewrite Python application", body="Rewrite server.py and deploy to production.")
        self.assertEqual(self.connector.qualify(data)["status"], "skipped")

    def test_observed_locale_and_slug_urls_resolve_to_one_identity(self):
        text = LISTING + "\n" + LISTING.replace("/bounty/", "/en/bounty/") + "/fix-pagination"
        self.assertEqual(bountyhub_links(text), [LISTING])
        invalid = [LISTING.replace("https://", "http://"), LISTING.replace("www.", "attacker@www."), LISTING.replace(".dev/", ".dev:443/"), LISTING + "/../../fake"]
        for url in invalid:
            self.assertEqual(bountyhub_links(url), [])


class BountyHubSearchTests(unittest.TestCase):
    def test_search_has_separate_durable_cursor_but_shared_github_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            http = Mock()
            http.get.return_value = json.dumps({"payload": {"results": [{"url": ISSUE}], "page_count": 3, "result_count": 20}})
            github = GitHub(http=http, state_store=store)
            source = BountyHub(http=http, state_store=store)
            github.search()
            github_state = copy.deepcopy(store.get_internal_state(GitHub.SEARCH_STATE_KEY))
            source.search()
            self.assertEqual(store.get_internal_state(GitHub.SEARCH_STATE_KEY), github_state)
            self.assertIs(source.http, github.http)
            resumed = BountyHub(http=http, state_store=Store(directory))
            resumed.search()
            params = parse_qs(urlsplit(http.get.call_args.args[0]).query)
            self.assertEqual(params["q"], [source.SEARCH_QUERIES[1]])
            self.assertIn("GitHub", resumed.last_status["detail"])
            self.assertIn("hela BountyHub-katalogen", resumed.last_status["detail"])
            resumed.search()
            params = parse_qs(urlsplit(http.get.call_args.args[0]).query)
            self.assertEqual(params["p"], ["2"])

    def test_source_error_does_not_claim_connection_or_advance_cursor(self):
        http = Mock()
        http.get.side_effect = ConnectorError("Offline")
        source = BountyHub(http=http)
        with self.assertRaises(ConnectorError):
            source.search()
        self.assertEqual(source.last_status["status"], "error")
        self.assertEqual(source._search_index, 0)


def catalog_row(**changes):
    # Exact API field shape verified on /api/bounties and /api/bounties/{uuid}.
    # Fictional task contents keep the fixture small; no account/session fields.
    row = {"id": LISTING.rsplit("/", 1)[-1], "title": "Docs: clarify README examples",
           "url": "https://api.github.com/repos/owner/project/issues/5", "htmlURL": ISSUE,
           "repositoryFullName": "owner/project", "issueNumber": 5, "issueState": "open",
           "claimed": False, "retracted": False, "solved": False, "isFrozen": False,
           "language": "Python", "additionalDescription": "Explain the supported options.",
           "totalAmount": "125.00", "amount": "10.00", "amountPaid": "137.50",
           "paymentStatus": "PROMISED", "assignmentType": "open", "deletedAt": None,
           "assignee": None, "claims": []}
    return {**row, **changes}


class BountyHubCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.http = Mock()
        self.http.get.return_value = json.dumps({"payload": {"results": [{"url": ISSUE}], "page_count": 1, "result_count": 1}})
        self.catalog = Mock()
        self.catalog.json.return_value = catalog_row()
        self.rates = Rates(fetch=lambda _: {"base": "EUR", "date": "2026-10-16", "rates": {"SEK": 11, "USD": 1.1}},
                           today=lambda: date(2026, 10, 18))
        self.source = BountyHub(http=self.http, catalog_http=self.catalog, state_store=self.store, rates=self.rates)

    def test_direct_catalogue_cursor_and_identity_survive_restart_separately(self):
        self.catalog.json.return_value = {"data": [catalog_row()], "hasNextPage": True}
        self.assertEqual(self.source.search(), [ISSUE])
        self.http.get.assert_not_called()
        params = parse_qs(urlsplit(self.catalog.json.call_args.args[0]).query)
        self.assertEqual(params["page"], ["1"])
        self.assertEqual(json.loads(params["filters"][0]), {"language": "", "repositoryFullName": "", "solved": False})
        saved = self.store.get_internal_state(self.source.CATALOG_STATE_KEY)
        self.assertEqual(saved["next_page"], 2)
        self.assertEqual(saved["issues"][ISSUE], [catalog_row()["id"]])
        self.assertIsNone(self.store.get_internal_state(GitHub.SEARCH_STATE_KEY))
        resumed = BountyHub(http=self.http, catalog_http=self.catalog, state_store=Store(self.tmp.name), rates=self.rates)
        self.catalog.json.return_value = {"data": [], "hasNextPage": False}
        self.assertEqual(resumed.search(), [])
        self.assertEqual(parse_qs(urlsplit(self.catalog.json.call_args.args[0]).query)["page"], ["2"])
        self.assertEqual(self.store.get_internal_state(self.source.CATALOG_STATE_KEY)["next_page"], 1)
        self.assertEqual(resumed.last_status["coverage"], "bountyhub_catalog")

    def test_failed_catalogue_falls_back_without_advancing_its_cursor(self):
        self.catalog.json.side_effect = ConnectorError("Catalog offline")
        self.assertEqual(self.source.search(), [ISSUE])
        self.assertEqual(self.source._catalog_page, 1)
        self.assertIsNone(self.store.get_internal_state(self.source.CATALOG_STATE_KEY))
        self.assertEqual(self.source.last_status["coverage"], "github_indexed_announcements")
        self.assertIn("GitHub-indexet", self.source.last_status["detail"])
        self.assertEqual(self.source.last_status["catalog_error"], "Catalog offline")

    def test_small_result_budget_keeps_unreturned_page_rows_across_restart(self):
        other = catalog_row(id="12345678-d052-467e-bfa2-ec3da3ab7884", issueNumber=6,
                            htmlURL="https://github.com/owner/project/issues/6", url="https://api.github.com/repos/owner/project/issues/6")
        self.catalog.json.return_value = {"data": [catalog_row(), other], "hasNextPage": True}
        self.assertEqual(self.source.search(limit=1), [ISSUE])
        resumed = BountyHub(http=self.http, catalog_http=self.catalog, state_store=Store(self.tmp.name), rates=self.rates)
        self.catalog.json.reset_mock()
        self.assertEqual(resumed.search(limit=1), [other["htmlURL"]])
        self.catalog.json.assert_not_called()
        self.assertEqual(self.store.get_internal_state(self.source.CATALOG_STATE_KEY)["pending"], [])
        self.assertEqual(self.store.get_internal_state(self.source.CATALOG_STATE_KEY)["next_page"], 2)

    def test_malformed_catalogue_does_not_advance_or_silently_report_empty(self):
        for payload in (None, {}, {"data": [], "hasNextPage": "true"}, {"data": [], "hasNextPage": True},
                        {"data": [catalog_row(htmlURL="https://evil.test/task")], "hasNextPage": False}):
            self.catalog.json.return_value = payload
            with self.subTest(payload=payload), self.assertRaises(ConnectorError):
                self.source._catalog_search(12)
            self.assertEqual(self.source._catalog_page, 1)
            self.assertIsNone(self.store.get_internal_state(self.source.CATALOG_STATE_KEY))

    def test_direct_catalogue_identity_allows_fresh_platform_advertisement_without_bot(self):
        self.catalog.json.return_value = {"data": [catalog_row()], "hasNextPage": False}
        self.source.search()
        self.catalog.json.return_value = catalog_row()
        data = issue()
        data["timelineItems"]["edges"] = []
        task = self.source.qualify(data, max_sek=1000)
        self.assertEqual(task["status"], "qualified")
        self.assertEqual(task["reward_amount"], 125)
        self.assertEqual(task["reward_currency"], "$")
        self.assertFalse(task["price_estimate"]["within_preference"])
        self.assertTrue(task["price_estimate"]["assumed_currency"])
        self.assertEqual(task["bountyhub_payment_status"], "PROMISED")
        self.assertEqual(task["source_facts"]["coverage"], "bountyhub_catalog")
        self.assertFalse(task["source_facts"]["reward_verified_paid"])
        self.assertFalse(task["source_facts"]["bountyhub_current_funding_verified"])
        self.catalog.json.assert_called_with(CATALOG_URL + "/" + catalog_row()["id"], timeout=20, limit=2_000_000)

    def test_detail_is_refetched_each_qualification_and_overrides_historical_bot_amount(self):
        self.catalog.json.side_effect = [catalog_row(totalAmount="120.00"), catalog_row(totalAmount="100.00"), catalog_row(retracted=True)]
        self.assertEqual(self.source.qualify(issue("$5"))["reward_amount"], 120)
        self.assertEqual(self.source.qualify(issue("$5"))["reward_amount"], 100)
        self.assertEqual(self.source.qualify(issue("$5"))["status"], "blocked")
        self.assertEqual(self.catalog.json.call_count, 3)

    def test_failed_detail_does_not_qualify_using_cached_or_historical_amount(self):
        self.catalog.json.side_effect = ConnectorError("Temporary API failure")
        with self.assertRaisesRegex(ConnectorError, "Temporary"):
            self.source.qualify(issue())
        self.catalog.json.side_effect = HTTPFailure(404)
        self.assertEqual(self.source.qualify(issue())["status"], "blocked")

    def test_detail_identity_cannot_redirect_work_to_another_issue(self):
        for change in ({"id": "12345678-d052-467e-bfa2-ec3da3ab7884"},
                       {"htmlURL": "https://github.com/other/project/issues/5"},
                       {"url": "https://api.github.com/repos/other/project/issues/5"}):
            self.catalog.json.return_value = catalog_row(**change)
            with self.subTest(change=change), self.assertRaises(ConnectorError):
                self.source.qualify(issue())

    def test_direct_platform_unavailable_reserved_or_active_claims_block(self):
        open_claim = {"isOpen": True, "isPaid": False, "pullRequestIsmerged": False, "rejectedAt": None}
        for change in ({"claimed": True}, {"retracted": True}, {"solved": True}, {"isFrozen": True},
                       {"issueState": "closed"}, {"deletedAt": "2026-10-20T00:00:00Z"},
                       {"assignmentType": "exclusive"}, {"assignee": {"username": "another"}},
                       {"claims": [open_claim]}, {"claims": [{**open_claim, "isOpen": False, "pullRequestIsmerged": True}]},
                       {"claims": [{**open_claim, "isOpen": False, "isPaid": True}]}):
            self.catalog.json.return_value = catalog_row(**change)
            with self.subTest(change=change):
                self.assertEqual(self.source.qualify(issue())["status"], "blocked")

    def test_rejected_or_closed_unmerged_claims_are_not_active(self):
        for claim in ({"isOpen": False, "isPaid": False, "pullRequestIsmerged": False, "rejectedAt": None},
                      {"isOpen": True, "isPaid": False, "pullRequestIsmerged": False, "rejectedAt": "2026-10-20T00:00:00Z"}):
            self.catalog.json.return_value = catalog_row(claims=[claim])
            self.assertEqual(self.source.qualify(issue())["status"], "qualified")

    def test_unknown_status_shapes_cannot_pass_platform_checks(self):
        for change in ({"claimed": "false"}, {"isFrozen": None}, {"assignmentType": "unknown"},
                       {"claims": None}, {"claims": [{}]}, {"totalAmount": "NaN"}, {"totalAmount": "-10.00"},
                       {"totalAmount": "10 USD"}, {"assignee": ""}, {"issueNumber": True}, {"additionalDescription": None}):
            self.catalog.json.return_value = catalog_row(**change)
            with self.subTest(change=change), self.assertRaises(ConnectorError):
                self.source.qualify(issue())

    def test_platform_only_requirements_are_both_scoped_and_passed_to_worker(self):
        requirements = "Fix parser.py and add regression tests."
        self.catalog.json.return_value = catalog_row(additionalDescription=requirements)
        data = issue()
        original_body = data["body"]
        task = self.source.qualify(data)
        self.assertEqual(task["specialist"], "code")
        enriched = self.source.work_issue(data, task)
        self.assertIn(requirements, enriched["body"])
        self.assertEqual(data["body"], original_body)
        self.catalog.json.return_value = catalog_row(additionalDescription="You must claim this before working.")
        self.assertEqual(self.source.qualify(issue())["status"], "blocked")

    def test_platform_language_can_route_small_python_task_without_title_keyword(self):
        data = issue()
        data.update(title="Fix parser edge case", body="Fix the number parser and add regression tests.")
        self.assertEqual(self.source.qualify(data)["specialist"], "code")
        self.assertEqual(self.source.qualify(data)["status"], "qualified")

    def test_platform_data_never_leaks_account_or_payment_session_fields(self):
        self.catalog.json.return_value = catalog_row(paymentStatus="PAID", stripeSessionId="not-for-display", issueOwner={"email": "not-for-display"}, pledges=[{"secret": "not-for-display"}])
        task = self.source.qualify(issue())
        self.assertEqual(task["status"], "qualified")
        self.assertNotIn("not-for-display", json.dumps(task))
        self.assertFalse(task["source_facts"]["reward_verified_paid"])
        self.assertEqual(task["reward_amount"], 125)

    def test_duplicate_issue_identities_remain_ambiguous_across_restart(self):
        self.catalog.json.return_value = {"data": [catalog_row(), catalog_row(id="12345678-d052-467e-bfa2-ec3da3ab7884")], "hasNextPage": False}
        self.assertEqual(self.source.search(), [ISSUE])
        self.catalog.json.reset_mock()
        resumed = BountyHub(http=self.http, catalog_http=self.catalog, state_store=Store(self.tmp.name), rates=self.rates)
        self.assertIn("Flera", resumed.qualify(issue())["reason"])
        self.catalog.json.assert_not_called()

    def test_original_issue_closed_is_rejected_before_platform_read(self):
        data = issue()
        data["state"] = "CLOSED"
        self.assertEqual(self.source.qualify(data)["status"], "blocked")
        self.catalog.json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
