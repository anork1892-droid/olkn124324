"""Public BountyHub catalogue and GitHub announcements, with bounded FX reads.

Platform records describe advertised rewards. They never prove income or escrow.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import re
import threading
import time
from urllib import parse

from .connectors import (
    ConnectorError, GitHub, HTTP, HTTPFailure, ISSUE_URL, advertised_reward,
    original_issue_reference, requires_code_or_runtime, walk,
)


RATE_URL = "https://api.frankfurter.dev/v1/latest?base=EUR&symbols=SEK,USD"
CATALOG_URL = "https://api.bountyhub.dev/api/bounties"
_ROLES = {"OWNER", "MEMBER", "COLLABORATOR"}
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_BOUNTY_PATH = re.compile(r"/(?:en/)?bounty/view/(" + _UUID + r")(?:/[A-Za-z0-9_-]+)?/?$", re.I)
_URL = re.compile(r"https?://[^\s<>\[\]()]+")


def bountyhub_links(text):
    """Return canonical, exact-domain listing links, preserving one identity."""
    links = []
    for raw in _URL.findall(text):
        try:
            url = parse.urlsplit(raw.rstrip(".,;!?"))
            valid = (url.scheme == "https" and url.hostname in {"bountyhub.dev", "www.bountyhub.dev"}
                     and not url.username and not url.password and url.port is None)
            match = _BOUNTY_PATH.fullmatch(url.path) if valid else None
        except ValueError:
            continue
        if match:
            links.append("https://www.bountyhub.dev/bounty/view/" + match[1].lower())
    return list(dict.fromkeys(links))


def _direct_comments(issue):
    """Never inherit comments from cross-referenced issues in a timeline."""
    issue_url = issue.get("url", "")
    if not isinstance(issue_url, str) or not ISSUE_URL.fullmatch(issue_url):
        return []
    pattern = re.compile(re.escape(issue_url) + r"#issuecomment-[1-9][0-9]*$")
    found = {}
    for key in ("timelineItems", "backTimelineItems"):
        timeline = issue.get(key, {})
        if not isinstance(timeline, dict):
            continue
        rows = list(timeline.get("nodes") or [])
        rows.extend(edge.get("node") for edge in timeline.get("edges", []) if isinstance(edge, dict))
        for node in rows:
            if not isinstance(node, dict) or node.get("__typename") != "IssueComment":
                continue
            if node.get("isHidden") or node.get("isMinimized"):
                continue
            url = node.get("url")
            if not isinstance(url, str) or not pattern.fullmatch(url) or not isinstance(node.get("body"), str):
                continue
            found[url] = node
    return sorted(found.values(), key=lambda node: (node.get("createdAt") or "", int(node["url"].rsplit("-", 1)[-1])))


def _unquoted(text):
    # Quoted prior comments are not new statements by the person quoting them.
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))


def _withdrawn(text):
    text = _unquoted(text)
    subject = r"(?:bount(?:y|ies)|bountyhub|reward)"
    removal = r"(?:removed|withdrawn|cancelled|canceled|retracted|refunded|no longer available)"
    return bool(re.search(
        rf"\b{removal}\b[^\n.!?]{{0,80}}\b{subject}\b|"
        rf"\b{subject}\b[^\n.!?]{{0,80}}\b{removal}\b|"
        rf"\b(?:do not|don't|will not|won't)\s+(?:accept|support)\b[^\n.!?]{{0,60}}\b{subject}\b",
        text, re.I,
    ) or re.search(rf"\b{subject}\b\s+(?:has been|was|is)\s+(?:closed|completed|paid(?: out)?|claimed)\b", text, re.I)
      or (re.search(rf"\b{subject}\b", text, re.I) and re.search(r"\b(?:test only|only a test|just a test)\b", text, re.I)))


def _platform_reward(text):
    amount, currency = advertised_reward(text)
    if currency == "$":
        # User-authorized USD estimation applies to unspecified dollars only.
        # Explicit Canadian/Australian/etc. dollar notation is not USD.
        codes = r"(?:CAD|AUD|NZD|HKD|SGD|MXN|ARS|TWD|BBD|BSD|BMD|BZD|JMD|NAD|TTD|XCD)"
        explicit = re.search(rf"\b({codes})\s*\$\s*[0-9]|\$\s*[0-9][0-9., ]*\s*({codes})\b", text, re.I)
        if explicit:
            currency = (explicit[1] or explicit[2]).upper()
    return amount, currency


def funding_evidence(issue):
    """Read real issue-author links or the bot's direct announcement comments.

    A contributor's quoted dollar amount, solution, or unrelated link never
    updates the advertised amount. A newer bot total replaces its contribution.
    """
    body = _unquoted(issue.get("body", ""))
    author = issue.get("author") or {}
    records = []
    links = bountyhub_links(body)
    if links:
        amount, currency = _platform_reward(issue.get("title", "") + "\n" + body)
        records.append({
            "kind": "issue_author", "source_url": issue["url"],
            "author": author.get("login", ""), "author_association": issue.get("authorAssociation", "UNKNOWN"),
            "published_at": issue.get("createdAt"), "links": links,
            "amount": amount, "currency": currency,
            "advertised": bool(re.search(r"\b(?:bounty|reward|pays?|payment|paid)\b", issue.get("title", "") + "\n" + body, re.I)),
        })
    for comment in _direct_comments(issue):
        if (comment.get("author") or {}).get("login", "").casefold() != "bountyhub-bot":
            continue
        text = _unquoted(comment["body"])
        links = bountyhub_links(text)
        if not links:
            continue
        # These are the two observed public bot templates. Do not interpret an
        # individual contribution as the total reward if the format changes.
        plain = text.replace("**", "")
        total = re.search(r"the total amount of the bounty is now\s*([^\n]+)", plain, re.I)
        initial = re.search(r"\ba bounty of\s*(.*?)\s+has been created by\b", plain, re.I)
        reward_text = total[1] if total else initial[1] if initial else ""
        amount, currency = _platform_reward(reward_text)
        records.append({
            "kind": "bountyhub_bot", "source_url": comment["url"],
            "author": "bountyhub-bot", "author_association": comment.get("authorAssociation", "UNKNOWN"),
            "published_at": comment.get("createdAt"), "links": links,
            "amount": amount, "currency": currency, "advertised": bool(reward_text),
        })
    return records


class Rates:
    """Free dated reference rates used only for selecting small tasks."""

    STATE_KEY = "bountyhub-fx-v1"
    MAX_AGE_DAYS = 7

    def __init__(self, state_store=None, fetch=None, today=None, clock=time.monotonic):
        self._store = state_store
        # Independent rate-source backoff must not pause GitHub discovery.
        self._http = HTTP(min_interval=1) if fetch is None else None
        self._fetch = fetch or self._fetch_public
        self._today = today or (lambda: datetime.now(timezone.utc).date())
        self._clock = clock
        self._lock = threading.Lock()
        self._retry_at = 0
        self._last_error = None
        self._cache = state_store.get_internal_state(self.STATE_KEY) if state_store else None

    def _fetch_public(self, url):
        # This endpoint gets no GitHub token and cannot redirect credentials or
        # change destination. It is separate from GitHub's backoff/cursor.
        if url != RATE_URL:
            raise ConnectorError("Otillåten adress för valutakurs.")
        return self._http.json(url, timeout=12, limit=16_000)

    def _validate(self, value):
        if not isinstance(value, dict) or value.get("base") != "EUR":
            raise ConnectorError("Valutakällan saknar en verifierbar basvaluta.")
        try:
            rate_date = date.fromisoformat(value["date"])
        except (KeyError, TypeError, ValueError):
            raise ConnectorError("Valutakällan saknar ett giltigt kursdatum.") from None
        if not 0 <= (self._today() - rate_date).days <= self.MAX_AGE_DAYS:
            raise ConnectorError("Valutakursen är framtida eller äldre än sju dagar.")
        rates = value.get("rates")
        if not isinstance(rates, dict) or any(
            type(rates.get(code)) not in {int, float} or not math.isfinite(rates[code]) or rates[code] <= 0
            for code in ("SEK", "USD")
        ):
            raise ConnectorError("Valutakällans kurser är ogiltiga.")
        if "amount" in value and (type(value["amount"]) not in {int, float} or value["amount"] != 1):
            raise ConnectorError("Valutakällan måste avse en EUR.")
        return {"base": "EUR", "date": rate_date.isoformat(), "rates": {key: float(rates[key]) for key in ("SEK", "USD")}}

    def _latest(self):
        with self._lock:
            cached = None
            try:
                cached = self._validate(self._cache)
            except ConnectorError:
                pass
            if cached and self._cache.get("fetched_on") == self._today().isoformat():
                return cached
            if self._clock() < self._retry_at:
                if cached:
                    return cached
                raise ConnectorError(self._last_error or "Valutakällan väntar på ett nytt försök.")
            try:
                value = self._validate(self._fetch(RATE_URL))
            except (ConnectorError, ValueError, TypeError, OSError) as exc:
                self._retry_at = self._clock() + 300
                self._last_error = str(exc) if isinstance(exc, ConnectorError) else "Valutakällan kunde inte kontrolleras."
                if cached:
                    return cached
                raise ConnectorError(self._last_error) from None
            self._cache = {**value, "fetched_on": self._today().isoformat()}
            self._retry_at = 0
            self._last_error = None
            if self._store:
                self._store.set_internal_state(self.STATE_KEY, self._cache)
            return value

    def estimate(self, amount, currency, *, assume_usd=True):
        conversion_currency = "USD" if currency == "$" and assume_usd else currency
        estimate = {
            "amount_sek": None, "rate_date": None, "rate_source": None,
            "currency": currency, "original_amount": amount, "selection_only": True,
            "conversion_currency": conversion_currency, "assumed_currency": conversion_currency != currency,
            "status": "unresolved", "reason": "",
        }
        if type(amount) not in {int, float} or not math.isfinite(amount) or amount <= 0:
            estimate["reason"] = "Ett positivt annonserat belopp saknas."
        elif currency == "SEK":
            estimate.update(amount_sek=round(amount, 2), status="available", reason="Angivet SEK-belopp; ingen omräkning.")
        elif conversion_currency not in {"USD", "EUR"}:
            estimate["reason"] = "Dollarvalutan är ospecificerad; gränsen i SEK kan inte kontrolleras." if currency == "$" else "Valutan stöds inte för gränsen i SEK."
        else:
            try:
                rates = self._latest()
                converted = amount * rates["rates"]["SEK"] / (rates["rates"]["USD"] if conversion_currency == "USD" else 1)
                if not math.isfinite(converted):
                    raise ConnectorError("Omräkningen gav inget giltigt belopp.")
                estimate.update(amount_sek=round(converted, 2), rate_date=rates["date"], rate_source=RATE_URL,
                                status="available", reason="Ungefärligt urvalsbelopp enligt daterad referenskurs; ingen intäkt.")
                if estimate["assumed_currency"]:
                    estimate["reason"] += " $ behandlas som USD för uppskattningen enligt din inställning; originalvalutan är ospecificerad."
            except ConnectorError as exc:
                estimate["reason"] = str(exc)
        return estimate


class BountyHub(GitHub):
    SEARCH_STATE_KEY = "bountyhub-search-v1"
    SEARCH_QUERIES = (
        'is:issue is:open no:assignee "bountyhub.dev"',
        "is:issue is:open no:assignee commenter:bountyhub-bot",
    )
    COVERAGE = "BountyHub-annonser som är offentligt indexerade på GitHub; hela BountyHub-katalogen och aktuellt deponerat belopp är inte kontrollerade."
    CATALOG_STATE_KEY = "bountyhub-catalog-v1"
    MAX_CATALOG_PAGES = 500

    def __init__(self, http=None, state_store=None, rates=None, catalog_http=None):
        super().__init__(http=http, state_store=state_store)
        self.rates = rates or Rates(state_store=state_store)
        # Alternative/test GitHub transports do not implicitly activate a new
        # real network transport. Tests can explicitly inject catalog_http.
        self.catalog_http = (HTTP(min_interval=1) if http is None or isinstance(http, HTTP) else None) if catalog_http is None else catalog_http or None
        self._catalog_lock = threading.Lock()
        self._catalog_page = 1
        self._catalog_issues = {}
        self._catalog_pending = []
        if state_store:
            saved = state_store.get_internal_state(self.CATALOG_STATE_KEY)
            if isinstance(saved, dict) and saved.get("version") == 1:
                page = saved.get("next_page")
                if type(page) is int and 1 <= page <= self.MAX_CATALOG_PAGES:
                    self._catalog_page = page
                issues = saved.get("issues")
                if isinstance(issues, dict):
                    for url, ids in list(issues.items())[-5000:]:
                        if isinstance(url, str) and ISSUE_URL.fullmatch(url) and isinstance(ids, list) and 1 <= len(ids) <= 20 and all(isinstance(value, str) and re.fullmatch(_UUID, value, re.I) for value in ids):
                            self._catalog_issues[url] = list(dict.fromkeys(value.lower() for value in ids))
                pending = saved.get("pending", [])
                if isinstance(pending, list) and len(pending) <= 10 and all(isinstance(url, str) and url in self._catalog_issues for url in pending):
                    self._catalog_pending = list(dict.fromkeys(pending))
        self.last_status = {"configured": True, "status": "unknown", "detail": self.COVERAGE}

    def search(self, query=None, limit=12):
        catalog_error = None
        if self.catalog_http and query is None:
            try:
                return self._catalog_search(limit)
            except ConnectorError as exc:
                catalog_error = str(exc)
        try:
            results = super().search(query=query, limit=limit)
        except ConnectorError as exc:
            detail = ("BountyHub-katalogen: " + catalog_error + " " if catalog_error else "") + str(exc) + " " + self.COVERAGE
            self.last_status = {"configured": True, "status": "error", "detail": detail, "coverage": "github_indexed_announcements"}
            raise
        detail = ("BountyHub-katalogen kunde inte läsas; använder GitHub-indexet. " if catalog_error else "") + self.COVERAGE
        self.last_status = {"configured": True, "status": "connected", "detail": detail, "coverage": "github_indexed_announcements"}
        if catalog_error:
            self.last_status["catalog_error"] = catalog_error
        return results

    @staticmethod
    def _catalog_record(value, *, detail=False):
        """Parse the verified public API shape; never retain payment/session data."""
        if not isinstance(value, dict):
            raise ConnectorError("BountyHub returnerade en ogiltig annons.")
        bounty_id, repo, number = value.get("id"), value.get("repositoryFullName"), value.get("issueNumber")
        if (not isinstance(bounty_id, str) or not re.fullmatch(_UUID, bounty_id, re.I)
                or not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
                or any(part in {".", ".."} for part in repo.split("/")) or type(number) is not int or number <= 0):
            raise ConnectorError("BountyHub-annonsens identitet kunde inte verifieras.")
        url = f"https://github.com/{repo}/issues/{number}"
        if value.get("htmlURL") != url or value.get("url") != f"https://api.github.com/repos/{repo}/issues/{number}":
            raise ConnectorError("BountyHub-annonsens GitHub-länkar stämmer inte överens.")
        if any(type(value.get(key)) is not bool for key in ("claimed", "retracted", "solved", "isFrozen")):
            raise ConnectorError("BountyHub-annonsens tillgänglighet kunde inte verifieras.")
        if value.get("issueState") not in {"open", "closed"} or value.get("assignmentType") not in {"open", "exclusive"}:
            raise ConnectorError("BountyHub-annonsens status eller reservationstyp är okänd.")
        if "assignee" not in value or value["assignee"] is not None and not isinstance(value["assignee"], dict):
            raise ConnectorError("BountyHub-annonsens tilldelning kunde inte verifieras.")
        if "deletedAt" not in value or value["deletedAt"] is not None and not isinstance(value["deletedAt"], str):
            raise ConnectorError("BountyHub-annonsens borttagningsstatus kunde inte verifieras.")
        title, requirements, language = value.get("title"), value.get("additionalDescription"), value.get("language")
        if not isinstance(title, str) or not title.strip() or len(title) > 2000 or not isinstance(requirements, str) or len(requirements) > 12000:
            raise ConnectorError("BountyHub-annonsens krav kunde inte läsas tillförlitligt.")
        if language is not None and (not isinstance(language, str) or len(language) > 100):
            raise ConnectorError("BountyHub-annonsens språkfält är ogiltigt.")
        amount = value.get("totalAmount")
        if not isinstance(amount, str) or not re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,2})?", amount):
            raise ConnectorError("BountyHub-annonsen saknar ett läsbart totalbelopp.")
        try:
            reward = float(Decimal(amount))
        except (InvalidOperation, ValueError, OverflowError):
            raise ConnectorError("BountyHub-annonsens belopp är ogiltigt.") from None
        if not math.isfinite(reward) or not 0 <= reward <= 100_000_000:
            raise ConnectorError("BountyHub-annonsens belopp är ogiltigt.")
        payment = value.get("paymentStatus")
        if not isinstance(payment, str) or not re.fullmatch(r"[A-Z_]{1,40}", payment):
            raise ConnectorError("BountyHub-annonsens betalningsuppgift är ogiltig.")
        record = {"id": bounty_id.lower(), "url": url, "repo": repo, "number": number, "title": title,
                  "requirements": requirements, "language": language, "amount": reward,
                  # The API currently omits a currency field. Its public UI
                  # displays dollars; the user's explicit USD-estimation choice
                  # remains visible rather than inventing currency metadata.
                  "currency": "$", "payment_status": payment, "assignment_type": value["assignmentType"],
                  "unavailable": value["claimed"] or value["retracted"] or value["solved"] or value["isFrozen"] or value["deletedAt"] is not None or value["issueState"] != "open",
                  "assigned": value["assignee"] is not None, "active_claims": False, "accepted_claims": False}
        if detail:
            claims = value.get("claims")
            if not isinstance(claims, list) or len(claims) > 1000:
                raise ConnectorError("BountyHub-annonsens registrerade leveranser kunde inte verifieras.")
            for claim in claims:
                if (not isinstance(claim, dict) or any(type(claim.get(key)) is not bool for key in ("isOpen", "isPaid", "pullRequestIsmerged"))
                        or "rejectedAt" not in claim or claim["rejectedAt"] is not None and not isinstance(claim["rejectedAt"], str)):
                    raise ConnectorError("En registrerad BountyHub-leverans har okänd status.")
                if claim["isPaid"] or claim["rejectedAt"] is None and (claim["isOpen"] or claim["pullRequestIsmerged"]):
                    record["active_claims"] = True
                if claim["isPaid"] or claim["rejectedAt"] is None and claim["pullRequestIsmerged"]:
                    record["accepted_claims"] = True
        return record

    def _catalog_search(self, limit):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ConnectorError("Ogiltig sidstorlek för BountyHub-sökningen.")
        # A fixed API page size keeps the saved page cursor stable across callers.
        with self._catalog_lock:
            if self._catalog_pending:
                result, pending = self._catalog_pending[:limit], self._catalog_pending[limit:]
                self._save_catalog_cursor(self._catalog_page, self._catalog_issues, pending)
                self._catalog_pending = pending
                self.last_search = {"page": None, "returned": len(result), "automatic": True, "coverage": "bountyhub_catalog", "from_saved_page": True}
                self.last_status = {"configured": True, "status": "connected", "coverage": "bountyhub_catalog",
                                    "detail": "Fortsätter med sparade träffar från BountyHub-katalogen. Den aktuella annonsen granskas före arbete."}
                return result
            page = self._catalog_page
            params = {"page": page, "limit": 10,
                      "filters": json.dumps({"language": "", "repositoryFullName": "", "solved": False}, separators=(",", ":")),
                      "sort": json.dumps([{"orderBy": "createdAt", "order": "desc"}, {"orderBy": "createdAt", "order": "desc"}], separators=(",", ":"))}
            payload = self.catalog_http.json(CATALOG_URL + "?" + parse.urlencode(params), timeout=20, limit=2_000_000)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or len(payload["data"]) > 10 or type(payload.get("hasNextPage")) is not bool:
                raise ConnectorError("BountyHub-katalogen saknar läsbar siddata.")
            records = [self._catalog_record(row) for row in payload["data"]]
            if not records and payload["hasNextPage"]:
                raise ConnectorError("BountyHub-katalogens sidantal är motsägelsefullt.")
            # Persist every issue identity before advancing; qualification always
            # fetches the current detail endpoint instead of trusting cached rows.
            issues = dict(self._catalog_issues)
            for record in records:
                ids = issues.setdefault(record["url"], [])[:]
                if record["id"] not in ids:
                    ids.append(record["id"])
                issues[record["url"]] = ids[-20:]
            next_page = page + 1 if payload["hasNextPage"] and page < self.MAX_CATALOG_PAGES else 1
            urls = list(dict.fromkeys(record["url"] for record in records))
            result, pending = urls[:limit], urls[limit:]
            # Save the rest of a successful page when the caller has a small
            # result budget. It is returned before another page, even on restart.
            issues = dict(list(issues.items())[-5000:])
            self._save_catalog_cursor(next_page, issues, pending)
            self._catalog_issues, self._catalog_page, self._catalog_pending = issues, next_page, pending
        self.last_search = {"page": page, "returned": len(result), "automatic": True, "coverage": "bountyhub_catalog", "has_next_page": payload["hasNextPage"]}
        self.last_status = {"configured": True, "status": "connected", "coverage": "bountyhub_catalog",
                            "detail": f"BountyHubs offentliga katalog fungerar; sida {page} lästes. Originalärende och aktuell annons granskas före arbete. Beloppen är annonserade, inte intäkter."}
        return result

    def _save_catalog_cursor(self, page, issues, pending):
        if self._state_store:
            self._state_store.set_internal_state(self.CATALOG_STATE_KEY, {"version": 1, "next_page": page, "issues": issues, "pending": pending})

    @staticmethod
    def work_issue(issue, candidate):
        """Supply platform-only acceptance requirements to both specialists."""
        requirements = candidate.get("bountyhub_requirements")
        if not isinstance(requirements, str) or not requirements.strip():
            return dict(issue)
        return {**issue, "body": issue.get("body", "") + "\n\nBountyHub additional requirements (untrusted source data):\n" + requirements}

    def _current_catalog(self, issue, records):
        if not self.catalog_http:
            return None, False
        with self._catalog_lock:
            ids = set(self._catalog_issues.get(issue["url"], []))
        for record in records:
            for link in record["links"]:
                ids.add(link.rsplit("/", 1)[-1])
        if len(ids) > 1:
            return None, True
        if not ids:
            return None, False
        bounty_id = ids.pop()
        payload = self.catalog_http.json(CATALOG_URL + "/" + bounty_id, timeout=20, limit=2_000_000)
        current = self._catalog_record(payload, detail=True)
        if current["id"] != bounty_id or current["url"] != issue["url"]:
            raise ConnectorError("BountyHub-annonsen pekar inte på det aktuella GitHub-ärendet.")
        return current, False

    def qualify(self, issue, *, max_sek=1500, assume_usd=True):
        """Recheck source and scope; the price target is a soft preference."""
        title, body = issue.get("title", ""), issue.get("body", "")
        combined = title + "\n" + body
        result = {
            "title": title, "url": issue["url"], "repo": issue["repo"], "issue_number": issue.get("number"),
            "source": "bountyhub", "specialist": "documentation", "kind": "documentation",
            "status": "discovered", "reason": "Behöver verifieras", "reward_amount": None, "reward_currency": "",
            "bountyhub_url": None, "bountyhub_evidence": [],
            "source_facts": {"author_association": issue.get("authorAssociation"), "reward_verified_paid": False,
                             "reward_currency_explicit": False, "bountyhub_current_funding_verified": False,
                             "project_acceptance_verified": False, "coverage": "github_indexed_announcements"},
            "price_estimate": None, "competition": False,
        }

        def reject(reason, status="blocked"):
            result.update(status=status, reason=reason)
            return result

        original = original_issue_reference(body)
        if original is not None:
            if original and original != issue["url"]:
                result["original_issue_url"] = original
                return reject("Återpublicerad annons. Originalärendet granskas separat: " + original, "skipped")
            return reject("Annonsens originalkälla är oklar eller saknar en direktlänk till ett annat GitHub-ärende.")
        if issue.get("state") != "OPEN":
            return reject("Uppdraget är inte öppet.")
        comments = _direct_comments(issue)
        if issue.get("authorAssociation") in _ROLES and _withdrawn(combined):
            return reject("Projektansvarig anger att belöningen är borttagen eller endast ett test.")
        for comment in comments:
            if (comment.get("authorAssociation") in _ROLES or (comment.get("author") or {}).get("login", "").casefold() == "bountyhub-bot") and _withdrawn(comment["body"]):
                result["bountyhub_withdrawal_url"] = comment["url"]
                return reject("Projektansvarig eller BountyHub-boten har dragit tillbaka belöningen, avböjt bounties eller angett att den var ett test.")
        records = funding_evidence(issue)
        try:
            catalog, ambiguous = self._current_catalog(issue, records)
        except HTTPFailure as exc:
            if exc.status_code == 404:
                return reject("BountyHub-annonsen finns inte längre kvar. Ingen historisk annons används för nytt arbete.")
            raise
        if ambiguous:
            return reject("Flera olika BountyHub-annonser anges för ärendet; belöningens källa är oklar.")
        if catalog:
            checked_at = datetime.now(timezone.utc).isoformat()
            listing = "https://www.bountyhub.dev/bounty/view/" + catalog["id"]
            records.append({"kind": "bountyhub_catalog", "source_url": CATALOG_URL + "/" + catalog["id"],
                            "links": [listing], "amount": catalog["amount"], "currency": catalog["currency"],
                            "advertised": True, "checked_at": checked_at})
            result["bountyhub_url"] = listing
            result.update(reward_amount=catalog["amount"], reward_currency=catalog["currency"])
            result["bountyhub_requirements"] = catalog["requirements"]
            result["bountyhub_payment_status"] = catalog["payment_status"]
            result["bountyhub_evidence"] = records
            result["source_facts"].update(coverage="bountyhub_catalog", platform_status_checked_at=checked_at)
            if catalog["unavailable"]:
                return reject("BountyHub anger att annonsen är löst, indragen, fryst, borttagen eller stängd.")
            if catalog["assignment_type"] == "exclusive" or catalog["assigned"]:
                return reject("BountyHub-uppdraget kräver tilldelning eller är redan tilldelat någon annan.")
            if catalog["accepted_claims"]:
                return reject("Det finns redan en mergad eller betald registrerad leverans på BountyHub.")
            result["competition"] = catalog["active_claims"]
            combined += "\n" + catalog["requirements"]
        result["bountyhub_evidence"] = records
        links = list(dict.fromkeys(link for record in records for link in record["links"]))
        if not links:
            return reject("Ingen direkt BountyHub-annons från ärendets författare eller BountyHub-boten kunde verifieras.", "skipped")
        if len(links) != 1:
            return reject("Flera olika BountyHub-annonser anges för ärendet; belöningens källa är oklar.")
        result["bountyhub_url"] = links[0]
        bot_records = [record for record in records if record["kind"] == "bountyhub_bot"]
        catalog_records = [record for record in records if record["kind"] == "bountyhub_catalog"]
        funding = catalog_records[-1] if catalog_records else bot_records[-1] if bot_records else records[0]
        reward, currency = funding["amount"], funding["currency"]
        result.update(reward_amount=reward, reward_currency=currency)
        result["source_facts"]["reward_currency_explicit"] = currency in {"SEK", "USD", "EUR"}
        result["source_facts"]["funding_advertised_by"] = funding["kind"]
        if not reward or not funding["advertised"]:
            return reject("BountyHub-länken saknar ett läsbart positivt totalbelopp eller betalningslöfte.")
        if not bot_records and not catalog_records:
            if re.search(r"proposed (?:bounty|reward)|suggested (?:bounty|reward)|bounty proposal|would (?:pay|offer)|could (?:pay|offer)|looking for (?:a )?sponsor", combined, re.I):
                return reject("Beloppet verkar vara ett förslag, inte ett betalningslöfte.", "skipped")
            if issue.get("authorAssociation") not in _ROLES:
                return reject("Den länkade belöningen saknar både BountyHub-botens annons och en verifierbar projektansvarig avsändare.")
        if issue.get("assignedActors", {}).get("nodes") or issue.get("assignees", {}).get("nodes"):
            return reject("Uppdraget är tilldelat någon annan.")
        labels = {str(node.get("name", "")).casefold() for node in walk(issue.get("labels", {}))}
        if re.search(r"claimed|reserved|in.progress|assigned", " ".join(labels), re.I):
            return reject("Uppdraget är reserverat eller pågår redan.")
        if re.search(r"comment\s+[`\"']?/(?:attempt|claim)|must.{0,25}(?:claim|assigned)|first.{0,12}assigned", combined, re.I):
            return reject("Uppdraget kräver en reservation innan arbete kan börja.")
        for key in ("linkedPullRequests", "closedByPullRequestsReferences"):
            pull_requests = issue.get(key, {}).get("nodes", [])
            if any(pr.get("state") == "MERGED" for pr in pull_requests):
                return reject("Det finns redan en mergad leverans för uppdraget.")
            if any(pr.get("state") == "OPEN" for pr in pull_requests):
                result["competition"] = True
        # Front/back fragments do not establish that the middle of a discussion
        # is complete. A missing reservation or withdrawal must not be ignored.
        for key in ("timelineItems", "backTimelineItems"):
            info = issue.get(key, {}).get("pageInfo", {})
            if info.get("hasNextPage") or info.get("hasPreviousPage"):
                return reject("Diskussionen är ofullständigt inläst; ledig omfattning är inte verifierad.")
        docs = bool(re.search(r"\b(docs?|documentation|readme|faq|typos?|spelling)\b", title, re.I) or labels.intersection({"docs", "documentation"})
                         or re.search(r"\b(?:add|write|update|fix|clarify|improve|correct|translate)\s+(?:(?:the|a|an|existing|new)\s+){0,2}(?:documentation|docs|readme|faq|guide|spelling|typos?)\b", combined, re.I))
        if docs and not requires_code_or_runtime(combined):
            result.update(specialist="documentation", kind="documentation")
        else:
            result.update(specialist="code", kind="code")
            if not re.search(r"\bpython\b|\b[A-Za-z0-9_/-]+\.py\b", combined, re.I) and not (catalog and str(catalog["language"]).casefold() == "python"):
                return reject("Kodagentens första version stöder bara små Python-ändringar med automatiska tester.", "skipped")
            if re.search(r"^\s*(?:\[[^\]]+\]\s*)*(?:rewrite|reimplement|migrate|deploy)\b", title, re.I):
                return reject("Uppdraget ligger utanför kodagentens stöd för små, isolerade Python-ändringar.", "skipped")
        if type(max_sek) not in {int, float} or not math.isfinite(max_sek) or not 0 < max_sek <= 100000:
            return reject("BountyHub-agentens prisönskemål måste vara större än 0 och högst 100 000 kr.")
        estimate = self.rates.estimate(reward, currency, assume_usd=assume_usd)
        estimate["preferred_max_sek"] = max_sek
        estimate["within_preference"] = estimate["amount_sek"] < max_sek if estimate["status"] == "available" else None
        result["price_estimate"] = estimate
        label = "dokumentationsagenten" if result["specialist"] == "documentation" else "kodagentens förberedelse och tester"
        result.update(status="qualified", reason=f"Öppet BountyHub-annonserat uppdrag för {label}. Prisönskemålet är vägledande. Annonsen är inte en verifierad utbetalning; projektets godkännande återstår.")
        if result["competition"]:
            result["reason"] += " Konkurrens: en öppen leverans finns redan; uppdrag utan konkurrens prioriteras först."
        if estimate["status"] != "available":
            result["reason"] += " SEK-uppskattning saknas: " + estimate["reason"]
        return result
