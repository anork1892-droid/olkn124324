"""Bounded public GitHub reads, optional GitHub delivery, and local-only inference.

Repository and issue content is data. None of it is executed as commands.
"""
from __future__ import annotations

import base64
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import PurePosixPath
import re
import ssl
import threading
import time
from urllib import error, parse, request

import certifi


class ConnectorError(RuntimeError):
    pass


class NetworkFailure(ConnectorError):
    """A retryable connection failure, distinct from an unsupported task."""


class RateLimited(ConnectorError):
    pass


class HTTPFailure(ConnectorError):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"Källan svarade HTTP {status_code}; anslutningen är inte verifierad.")


class RedirectBlocked(ConnectorError):
    """A source needs a different access route, rather than a timed retry."""


class LoginRedirect(RedirectBlocked):
    """Public web read redirected to login; the public API may still work."""


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = parse.urlsplit(newurl)
        if target.scheme == "https" and target.hostname == "github.com" and target.path in {"/login", "/session"}:
            raise LoginRedirect("GitHub kräver inloggning för att visa källan. BountyOS offentliga anslutning kan inte läsa uppdraget. Kontrollera åtkomsten via Visa på GitHub.")
        raise RedirectBlocked("Källan skickar vidare till en annan adress. Länken behöver kontrolleras innan uppdraget kan granskas; automatiska omförsök är pausade.")


class HTTP:
    """TLS verification stays on; credentials never follow redirects."""

    def __init__(self, local=False, min_interval=1.0, clock=time.monotonic):
        self.local = local
        self.min_interval = min_interval
        self.clock = clock
        self._lock = threading.Lock()
        self._next = 0.0
        self._blocked_until = 0.0
        self._failures = 0
        self._last_failure = None
        # Keep system/custom trust and add the application's pinned CA bundle.
        # Windows Python installations can lack an issuer that certifi supplies.
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=certifi.where())
        handlers = [NoRedirect(), request.HTTPSHandler(context=context)]
        if local:
            handlers.append(request.ProxyHandler({}))
        self.opener = request.build_opener(*handlers)

    def get(self, url, *, payload=None, method=None, token=None, timeout=25, limit=4_000_000):
        parsed = parse.urlsplit(url)
        if self.local:
            validate_local_url(url, allow_path=True)
        elif parsed.scheme != "https" or parsed.hostname not in {
            "github.com", "api.github.com", "raw.githubusercontent.com", "api.frankfurter.dev", "api.bountyhub.dev"
        }:
            raise ConnectorError("Otillåten källadress.")
        if token and (parsed.scheme != "https" or parsed.hostname != "api.github.com"):
            raise ConnectorError("GitHub-autentisering får endast skickas till GitHub API.")
        with self._lock:
            now = self.clock()
            if now < self._blocked_until:
                cause = f" Orsak: {self._last_failure}" if self._last_failure else ""
                raise RateLimited(f"Källan har pausats efter ett nätverksfel. Försök om {int(self._blocked_until-now)+1} s." + cause)
            delay = max(0, self._next - now)
            self._next = max(now, self._next) + self.min_interval
        if delay:
            time.sleep(delay)
        headers = {"User-Agent": "BountyOS/0.1 (public bounty research)", "Accept": "application/json, text/html"}
        if token:
            headers.update(Authorization="Bearer " + token, **{"X-GitHub-Api-Version": "2022-11-28"})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise ConnectorError("Svaret är större än den tillåtna storleken.")
                result = raw.decode("utf-8")
            with self._lock:
                if self.clock() >= self._blocked_until:
                    self._failures = 0
                    self._last_failure = None
            return result
        except error.HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504}:
                self._backoff(exc.headers.get("Retry-After"), reason=str(HTTPFailure(exc.code)))
            if exc.code == 429:
                raise RateLimited("GitHub begränsade sökningen (HTTP 429); automatisk återhämtning är aktiverad.") from None
            raise HTTPFailure(exc.code) from None
        except (error.URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            cause = exc.reason if isinstance(exc, error.URLError) else exc
            if isinstance(cause, ssl.SSLCertVerificationError):
                message = "Python kunde inte verifiera webbplatsens HTTPS-certifikat. Kontrollera datorns datum och betrodda certifikat."
            else:
                message = "Anslutningen misslyckades. Kontrollera nätverk och att tjänsten körs."
            self._backoff(reason=message)
            raise NetworkFailure(message) from None

    def _backoff(self, retry_after=None, *, reason=None):
        with self._lock:
            self._failures += 1
            try:
                seconds = float(retry_after)
            except (ValueError, TypeError):
                seconds = min(1800, 30 * (2 ** min(self._failures, 6)))
            self._blocked_until = max(self._blocked_until, self.clock() + min(3600, max(30, seconds)))
            self._last_failure = reason

    def retry_after_seconds(self):
        with self._lock:
            return max(0.0, self._blocked_until - self.clock())

    def json(self, url, **kwargs):
        try:
            return json.loads(self.get(url, **kwargs))
        except (ValueError, TypeError):
            raise ConnectorError("Tjänsten returnerade ogiltig JSON.") from None


def validate_local_url(url, allow_path=False):
    try:
        parts = parse.urlsplit(url)
        host = parts.hostname
        local = host == "localhost" or ipaddress.ip_address(host or "").is_loopback
        valid = parts.scheme in {"http", "https"} and local and not parts.username and not parts.password
        valid = valid and not parts.query and not parts.fragment and (allow_path or parts.path in {"", "/"})
        _ = parts.port
    except ValueError:
        valid = False
    if not valid:
        raise ConnectorError("Budgeten är 0 kr: Ollama måste finnas på en lokal loopback-adress.")
    return url.rstrip("/")


class JSONScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.values = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type") == "application/json":
            self.active, self.parts = True, []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.active = False
            try:
                self.values.append(json.loads("".join(self.parts)))
            except ValueError:
                pass


def embedded_json(document):
    # GitHub's content negotiation sometimes returns the route payload as raw JSON.
    try:
        data = json.loads(document)
        if isinstance(data, (dict, list)):
            return [data]
    except (ValueError, TypeError):
        pass
    parser = JSONScripts()
    parser.feed(document)
    return parser.values


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


ISSUE_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)$")


def issue_parts(url):
    match = ISSUE_URL.fullmatch(url)
    if not match:
        raise ConnectorError("Ogiltig GitHub issue-adress.")
    return match.group(1), int(match.group(2))


def safe_doc_path(path):
    if not isinstance(path, str) or not path or len(path) > 180 or "\\" in path or "%" in path:
        return False
    parts = path.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        return False
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", path):
        return False
    file = PurePosixPath(path)
    if re.fullmatch(r"(?:agents?|skills?|secrets?|credentials?)(?:[._-].*)?\.(?:md|txt)", file.name, re.I):
        return False
    return (len(parts) == 1 or parts[0] in {"docs", "doc", "documentation"}) and file.suffix.lower() in {".md", ".txt"}


def requested_document_paths(issue):
    """Read path mentions as data; only direct creation requests permit new files."""
    text = str(issue.get("title", "")) + "\n" + str(issue.get("body", ""))
    # Include the whole path-like token so unsafe prefixes cannot turn into a
    # seemingly safe suffix (../docs/a.md, absolute paths, URLs, encoded paths).
    tokens = re.finditer(
        r"(?<![\w./\\%:-])[A-Za-z0-9_./\\%:-]+\.(?:md|txt)"
        r"(?![\w/\\%:-]|\.[\w./\\%:-])", text, re.I,
    )
    mentioned, created = [], []
    creation = re.compile(
        r"\b(?:create|add|write\s+(?:(?:a|the)\s+)?new)"
        r"(?:\s+(?:a|an|the|new|documentation|document|doc|docs|markdown|text|file|guide|page|tutorial|faq|readme|at|as|named|called|in)){0,12}"
        r"\s*[:`\"'\[]*\s*$", re.I,
    )
    for match in tokens:
        path = match.group()
        if not safe_doc_path(path):
            continue
        mentioned.append(path)
        prefix = re.split(r"[\n.!?;]", text[:match.start()])[-1][-180:]
        action = creation.search(prefix)
        if not action:
            continue
        before_action = prefix[:action.start()]
        if re.search(r"\b(?:not|never|without|avoid|don't|no need to|rather than)\b", before_action, re.I):
            continue
        created.append(path)
    return list(dict.fromkeys(mentioned)), list(dict.fromkeys(created))


def repository_commit(roots, branch):
    """Use only repository-route metadata, not commit IDs in readme/link data."""
    candidates = set()
    pending = list(roots)
    while pending:
        item = pending.pop()
        if not isinstance(item, dict):
            continue
        for key in ("payload", "codeViewRepoRoute", "codeViewRoute"):
            if isinstance(item.get(key), dict):
                pending.append(item[key])
        if item.get("path") not in {None, "", "/"}:
            continue
        ref = item.get("refInfo", {})
        if not isinstance(ref, dict) or ref.get("name") not in {None, branch}:
            continue
        current = item.get("currentCommit", {})
        values = [item.get("currentOid"), ref.get("currentOid")]
        if isinstance(current, dict):
            values.append(current.get("oid"))
        for value in values:
            if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{40}", value):
                candidates.add(value.lower())
    return next(iter(candidates)) if len(candidates) == 1 else None


def delivery_schema(paths):
    """Constrain local generation to the validated documentation targets."""
    paths = sorted(set(path for path in paths if safe_doc_path(path)))
    if not paths:
        raise ConnectorError("Inga inlästa dokument kan användas för leveransen.")
    return {
        "type": "object", "additionalProperties": False,
        "required": ["files", "summary"],
        "properties": {
            "files": {
                "type": "array", "minItems": 0, "maxItems": 6,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["path", "content"],
                    "properties": {
                        "path": {"type": "string", "enum": paths},
                        "content": {"type": "string", "minLength": 1, "maxLength": 80000},
                    },
                },
            },
            "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
        },
    }


def validate_delivery(value):
    if not isinstance(value, dict) or not isinstance(value.get("files"), list) or not 1 <= len(value["files"]) <= 6:
        raise ConnectorError("Leveransen måste innehålla 1–6 dokumentationsfiler.")
    if not isinstance(value.get("summary"), str) or not 1 <= len(value["summary"]) <= 4000:
        raise ConnectorError("Leveransen saknar en giltig sammanfattning.")
    total, seen = 0, set()
    for entry in value["files"]:
        if not isinstance(entry, dict) or not safe_doc_path(entry.get("path")):
            raise ConnectorError("Leveransen försöker ändra en otillåten sökväg.")
        if entry["path"].casefold() in seen:
            raise ConnectorError("Leveransen innehåller samma fil flera gånger.")
        seen.add(entry["path"].casefold())
        content = entry.get("content")
        if not isinstance(content, str) or not content.strip() or "\x00" in content:
            raise ConnectorError("Dokumentet har ogiltigt innehåll.")
        size = len(content.encode("utf-8"))
        total += size
        if size > 80_000 or total > 180_000:
            raise ConnectorError("Leveransen är för stor för automatisk granskning.")
        if re.search(r"<\s*(script|iframe|object|embed)\b|javascript\s*:", content, re.I):
            raise ConnectorError("Aktivt HTML-innehåll är inte tillåtet i leveransen.")
    return value


class GitHub:
    MAX_SEARCH_PAGES = 100
    SEARCH_STATE_KEY = "github-search-v1"
    SEARCH_QUERIES = (
        "is:issue is:open no:assignee bounty documentation in:title",
        "is:issue is:open no:assignee bounty readme in:title",
        "is:issue is:open no:assignee bounty docs in:title",
        "is:issue is:open no:assignee bounty typo in:title",
        "is:issue is:open no:assignee label:bounty documentation in:title",
        "is:issue is:open no:assignee label:bounty readme in:title",
        "is:issue is:open no:assignee label:bounty docs in:title",
        "is:issue is:open no:assignee bounty faq in:title",
        "is:issue is:open no:assignee bounty spelling in:title",
        "is:issue is:open no:assignee label:bounty faq in:title",
        "is:issue is:open no:assignee label:bounty spelling in:title",
        "is:issue is:open no:assignee label:documentation bounty in:body",
        "is:issue is:open no:assignee label:docs bounty in:body",
    )

    def __init__(self, http=None, state_store=None):
        self.http = http or HTTP(min_interval=3)
        self._state_store = state_store
        self._search_index = 0
        self._search_pages = {query: 1 for query in self.SEARCH_QUERIES}
        if state_store is not None:
            saved = state_store.get_internal_state(self.SEARCH_STATE_KEY)
            if isinstance(saved, dict) and type(saved.get("version")) is int and saved["version"] == 1:
                pages = saved.get("pages")
                if isinstance(pages, dict):
                    for query in self.SEARCH_QUERIES:
                        page = pages.get(query)
                        if type(page) is int and 1 <= page <= self.MAX_SEARCH_PAGES:
                            self._search_pages[query] = page
                next_query = saved.get("next_query")
                if next_query in self.SEARCH_QUERIES:
                    self._search_index = self.SEARCH_QUERIES.index(next_query)
        self.last_search = None
        self.last_status = {"configured": True, "status": "unknown", "detail": "Offentlig GitHub-sökning är ännu inte kontrollerad."}

    def search(self, query=None, limit=12):
        automatic = query is None
        if automatic:
            query = self.SEARCH_QUERIES[self._search_index]
        page = self._search_pages[query] if automatic else 1
        url = "https://github.com/search?" + parse.urlencode({"q": query, "type": "issues", "s": "updated", "o": "desc", "p": page})
        document = self.http.get(url)
        results = []
        routes = list(embedded_json(document))
        has_results = False
        saw_rows = False
        page_count = None
        result_count = None
        # Only route payloads contain search results. Unrelated page metadata and
        # references nested inside a result must not become additional tasks.
        while routes:
            route = routes.pop()
            if not isinstance(route, dict):
                continue
            routes.extend(route[key] for key in ("payload", "blackbirdSearchRoute") if key in route)
            rows = route.get("results")
            if not isinstance(rows, list):
                continue
            if route.get("errors"):
                raise ConnectorError("GitHub kunde inte slutföra sökfrågan; sökningen behöver kontrolleras.")
            has_results = True
            saw_rows = saw_rows or bool(rows)
            # GitHub's route declares the total pages explicitly. A short page
            # can still have successors, so the number of rows is not an end marker.
            if type(route.get("page_count")) is int and route["page_count"] >= 0:
                page_count = route["page_count"]
            if type(route.get("result_count")) is int and route["result_count"] >= 0:
                result_count = route["result_count"]
            for item in rows:
                if not isinstance(item, dict):
                    continue
                candidates = [item.get("url"), item.get("issue_url")]
                # Some rows carry repository + issue number instead of a URL.
                repo = item.get("repo")
                repository = repo.get("repository") if isinstance(repo, dict) else None
                number = item.get("number")
                if isinstance(repository, dict) and type(number) is int and number > 0:
                    owner, name = repository.get("owner_login"), repository.get("name")
                    if isinstance(owner, str) and isinstance(name, str):
                        candidates.append(f"https://github.com/{owner}/{name}/issues/{number}")
                for candidate in candidates:
                    if not isinstance(candidate, str):
                        continue
                    if candidate.startswith("/"):
                        candidate = "https://github.com" + candidate
                    if ISSUE_URL.fullmatch(candidate):
                        results.append(candidate)
                        break
        if not has_results:
            raise ConnectorError("GitHubs söksida saknar läsbar sökdata; sökningen behöver kontrolleras.")
        if saw_rows and not results:
            raise ConnectorError("GitHubs sökresultat kunde inte läsas tillförlitligt.")
        if automatic:
            bound = min(self.MAX_SEARCH_PAGES, page_count) if page_count is not None else self.MAX_SEARCH_PAGES
            next_pages = {**self._search_pages, query: page + 1 if results and page < bound else 1}
            next_index = (self._search_index + 1) % len(self.SEARCH_QUERIES)
            # Save only a successfully parsed page. Failed requests and malformed
            # responses retry this same query/page, including after an app restart.
            if self._state_store is not None:
                self._state_store.set_internal_state(self.SEARCH_STATE_KEY, {
                    "version": 1,
                    "next_query": self.SEARCH_QUERIES[next_index],
                    "pages": next_pages,
                })
            self._search_pages = next_pages
            self._search_index = next_index
        result_urls = list(dict.fromkeys(results))[:limit]
        self.last_search = {"query": query, "page": page, "page_count": page_count,
                            "result_count": result_count, "returned": len(result_urls), "automatic": automatic}
        self.last_status = {"configured": True, "status": "connected", "detail": "Offentlig GitHub-sökning fungerar."}
        return result_urls

    @staticmethod
    def _issue_author_association(issue, repo):
        author = issue.get("author")
        login = author.get("login") if isinstance(author, dict) else None
        if isinstance(login, str) and login and login.casefold() == repo.split("/")[0].casefold():
            return "OWNER"
        association = issue.get("authorAssociation")
        if isinstance(association, str) and association and association != "UNKNOWN":
            return association
        if not isinstance(login, str) or not login:
            return "UNKNOWN"
        issue_url = issue.get("url")
        if not isinstance(issue_url, str) or not ISSUE_URL.fullmatch(issue_url):
            return "UNKNOWN"
        comment_url = re.compile(re.escape(issue_url) + r"#issuecomment-[1-9][0-9]*$")
        # Front and back pages carry role metadata even when the issue itself
        # does not. Read only direct comments belonging to this exact issue.
        for key in ("timelineItems", "backTimelineItems"):
            timeline = issue.get(key)
            if not isinstance(timeline, dict):
                continue
            comments = []
            if isinstance(timeline.get("edges"), list):
                comments.extend(edge.get("node") for edge in timeline["edges"] if isinstance(edge, dict))
            if isinstance(timeline.get("nodes"), list):
                comments.extend(timeline["nodes"])
            for comment in comments:
                if not isinstance(comment, dict) or comment.get("__typename") != "IssueComment":
                    continue
                author = comment.get("author")
                comment_login = author.get("login") if isinstance(author, dict) else None
                url = comment.get("url")
                if not isinstance(comment_login, str) or comment_login.casefold() != login.casefold():
                    continue
                if not isinstance(url, str) or not comment_url.fullmatch(url):
                    continue
                association = comment.get("authorAssociation")
                if isinstance(association, str) and association and association != "UNKNOWN":
                    return association
        return "UNKNOWN"

    def issue(self, url):
        repo, number = issue_parts(url)
        try:
            document = self.http.get(url)
        except LoginRedirect:
            return self._public_api_issue(url)
        for root in embedded_json(document):
            for item in walk(root):
                if item.get("number") == number and item.get("url") == url and isinstance(item.get("body"), str) and "state" in item:
                    issue = dict(item)
                    issue["repo"] = repo
                    if any(issue.get(key, {}).get("pageInfo", {}).get(flag)
                           for key in ("timelineItems", "backTimelineItems")
                           for flag in ("hasNextPage", "hasPreviousPage")):
                        return self._public_api_issue(url)
                    issue["authorAssociation"] = self._issue_author_association(issue, repo)
                    return issue
        raise ConnectorError("GitHub-ärendet kunde inte läsas tillförlitligt.")

    def _public_api_issue(self, url):
        from .github_api import read_issue
        return read_issue(self.http, url)

    def repository_context(self, issue):
        """Read bounded docs and verify explicit new targets at a pinned snapshot."""
        repo = issue["repo"]
        try:
            root = self.http.get(f"https://github.com/{repo}")
        except LoginRedirect:
            from .github_api import documentation_context
            return documentation_context(self.http, issue)
        branch = None
        requested, creation_targets = requested_document_paths(issue)
        candidates = requested + ["README.md", "FAQ.md"]
        roots = embedded_json(root)
        for data in roots:
            for item in walk(data):
                if isinstance(item.get("defaultBranch"), str):
                    branch = item["defaultBranch"]
                path = item.get("path")
                if safe_doc_path(path):
                    candidates.append(path)
        if not branch:
            from .github_api import documentation_context
            return documentation_context(self.http, issue)
        commit = repository_commit(roots, branch)
        ref = commit or branch
        # Root listing normally does not include files under docs/.
        try:
            docs_page = self.http.get(f"https://github.com/{repo}/tree/{parse.quote(ref, safe='')}/docs")
            for data in embedded_json(docs_page):
                for item in walk(data):
                    if safe_doc_path(item.get("path")):
                        candidates.append(item["path"])
        except LoginRedirect:
            from .github_api import documentation_context
            return documentation_context(self.http, issue)
        except HTTPFailure as exc:
            if exc.status_code != 404:
                exc.args = ("Dokumentlistan kunde inte hämtas: " + str(exc),)
                raise
        documents, missing_targets = {}, []
        checked = list(dict.fromkeys(candidates))[:10]
        for path in checked:
            try:
                text = self.http.get(f"https://raw.githubusercontent.com/{repo}/{parse.quote(ref, safe='')}/{path}", limit=100_000)
                documents[path] = text
            except HTTPFailure as exc:
                if exc.status_code == 404:
                    if path in creation_targets:
                        missing_targets.append(path)
                    continue
                exc.args = ("Dokumentet " + path + " kunde inte hämtas: " + str(exc),)
                raise
            except LoginRedirect:
                from .github_api import documentation_context
                return documentation_context(self.http, issue)
            except ConnectorError as exc:
                raise type(exc)("Dokumentet " + path + " kunde inte hämtas: " + str(exc)) from None
        if not documents:
            raise ConnectorError("Inga stödda dokument hittades i målprojektet. Kontrollerade sökvägar: " + ", ".join(checked))
        context = {"repo": repo, "branch": branch, "files": documents, "new_files": missing_targets if commit else []}
        if commit:
            context["commit"] = commit
        elif missing_targets:
            context["new_files_unavailable_reason"] = "Nya dokument kunde inte godkännas eftersom underlagets exakta Git-version inte kunde verifieras."
        return context


def parse_reward_amount(raw):
    """Parse a complete money token; never salvage a numeric suffix/prefix."""
    raw = re.sub(r"[ \u00a0\u202f]+", " ", raw.strip())
    if len(raw) > 40:
        return None
    if re.fullmatch(r"[0-9]+(?:[.,][0-9]{1,2})?", raw):
        normalized = raw.replace(",", ".")
    elif re.fullmatch(r"[1-9][0-9]{0,2}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?", raw):
        normalized = raw.replace(",", "")
    elif re.fullmatch(r"[1-9][0-9]{0,2}(?:\.[0-9]{3})+,[0-9]{1,2}", raw):
        normalized = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"[1-9][0-9]{0,2}(?:\.[0-9]{3}){2,}", raw):
        normalized = raw.replace(".", "")
    elif re.fullmatch(r"[1-9][0-9]{0,2}(?: [0-9]{3})+(?:[.,][0-9]{1,2})?", raw):
        normalized = raw.replace(" ", "").replace(",", ".")
    else:
        # A lone dot followed by three digits is ambiguous (1.500).
        return None
    return float(normalized)


def advertised_reward(text):
    # Capture the entire numeric-looking expression, including unsupported
    # ranges/exponents/grouping, so malformed values cannot become smaller ones.
    token = r"[+\-−]?[0-9][0-9.,'’eE+\-−–—/%]*"
    token += r"(?:[ \u00a0\u202f]+(?:[+\-−–—/][ \u00a0\u202f]*)?[0-9][0-9.,'’eE+\-−–—/%]*)*"
    left = r"(?<![\w.,'’+\-−–—/])"
    right = r"(?![\w.,'’+\-−–—/%])"
    explicit = re.search(
        rf"\b(?P<prefix>USD|EUR|SEK)\s*(?P<before>{token}){right}|"
        rf"{left}(?P<after>{token})\s*(?P<suffix>USD|EUR|SEK)\b",
        text, re.I,
    )
    if explicit:
        currency = (explicit.group("prefix") or explicit.group("suffix")).upper()
        raw = explicit.group("before") or explicit.group("after")
        # Sentence punctuation after a currency-first amount is not a decimal.
        if explicit.group("prefix") and raw[-1:] in {".", ","} and raw[-2:-1] not in {".", ","}:
            raw = raw[:-1]
        return parse_reward_amount(raw), currency
    dollar = re.search(rf"\$\s*(?P<amount>{token}){right}", text)
    if dollar:
        raw = dollar.group("amount")
        if raw[-1:] in {".", ","} and raw[-2:-1] not in {".", ","}:
            raw = raw[:-1]
        return parse_reward_amount(raw), "$"
    return None, ""


def original_issue_reference(body):
    """Follow explicit source fields, never arbitrary links in a description."""
    label = r"(?:Source[ \t]+URL|Original[ \t]+URL|原[ \t]*URL|原始链接)"
    sections = re.findall(r"^#{1,6}[ \t]+[^\n]*(?:\bSource URL\b|\bOriginal URL\b|原[ \t]*URL|原始链接)[^\n]*\n(.*?)(?=^#{1,6}[ \t]|\Z)", body, re.I | re.M | re.S)
    for key, value in re.findall(r"^[ \t]*\|[ \t]*([^|\n]+?)[ \t]*\|[ \t]*([^|\n]*)\|[ \t]*$", body, re.M):
        # Repost catalogues also publish the original URL as a metadata row.
        # Requiring a complete field name avoids treating "See also" as a source.
        key = key.strip().strip("*` ")
        if re.fullmatch(label + r"(?:[ \t]*/[ \t]*" + label + r")*", key, re.I):
            sections.append(value)
    if not sections:
        return None
    originals = []
    for section in sections:
        # Markdown links can repeat the same URL in the label and target.
        urls = list(dict.fromkeys(re.findall(r"https?://[^\s<>\[\]()]+", section)))
        if len(urls) != 1 or not ISSUE_URL.fullmatch(urls[0]):
            return ""
        originals.append(urls[0])
    unique = list(dict.fromkeys(originals))
    return unique[0] if len(unique) == 1 else ""


def requires_code_or_runtime(text):
    """Recognize requested operations, not subjects such as GPU documentation.

    This is an initial scope filter; the worker still needs repository evidence
    and an independent review. A documentation explanation or a negation applies
    only to its local clause, never to later instructions in the same issue.
    """
    operations = re.compile(r"""\b(?:
        implement(?:ing)?
        |(?:add|write|create)\s+(?:(?:new|unit|integration|regression|automated|end-to-end)\s+){0,3}tests?
        |(?:fix|modify|change|update|write)\s+(?:(?:the|a|an|existing|new)\s+)?(?:code|implementation|function|method|class|endpoint|api|smart\s+contract)
        |(?:build|create|add)\s+(?:(?:the|a|an|new|working|GPU)\s+){0,3}(?:feature|function|method|class|endpoint|api|application|app|smart\s+contract|benchmark|test\s+suite)
        |(?:deploy|deploying|redeploy)
        |(?:run|running|execute|executing|perform|performing)\s+(?:(?:the|a|an|new|existing|GPU|unit|integration|regression|automated|runtime|performance|load)\s+){0,4}(?:benchmarks?|tests?|commands?|code|measurements?|deployments?|pytest|npm|python)
        |(?:benchmark|profile|test)\s+(?:(?:the|a|an|existing|new)\s+)?(?:code|application|app|software|system|GPU|performance|deployment|api)
        |measure\s+(?:(?:the|a|an|actual|runtime)\s+)?(?:performance|latency|throughput|memory|power)
    )\b""", re.I | re.X)
    for operation in operations.finditer(text):
        # Delimit conjunctions too: "document X and deploy Y" is mixed work.
        prefix = re.split(r"[\n.!?;,]|\b(?:and|but|then)\b", text[:operation.start()], flags=re.I)[-1]
        prefix = prefix.rstrip(" \t`'\"")
        if re.search(r"\b(?:do not|don't|must not|should not|never|without|no need to|not required to|no)(?:\s+(?:actually|ever|first))?\s*$", prefix, re.I):
            continue
        if re.search(r"\b(?:document|explain|describe|clarify|docs?|documentation|readme|faq|guide|tutorial|instructions|section)\b.{0,100}\b(?:how to|how users can|steps to|commands to)\s*$", prefix, re.I):
            continue
        return True
    return False


def qualify(issue):
    title, body = issue.get("title", ""), issue.get("body", "")
    combined = title + "\n" + body
    reward, currency = advertised_reward(combined)
    result = {"title": title, "url": issue["url"], "repo": issue["repo"], "kind": "documentation", "reward_amount": reward, "reward_currency": currency or "",
              "issue_number": issue.get("number"), "status": "discovered", "reason": "Behöver verifieras", "source_facts": {"author_association": issue.get("authorAssociation"), "reward_verified_paid": False, "reward_currency_explicit": currency in {"USD", "EUR", "SEK"}}}
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
    if not reward or not re.search(r"bounty|reward|pays?\b|payment|paid\b|belöning|ersättning", combined, re.I):
        return reject("Ingen uttrycklig ersättning kunde verifieras.", "skipped")
    if re.search(r"proposed (?:bounty|reward)|suggested (?:bounty|reward)|bounty proposal|would (?:pay|offer)|could (?:pay|offer)|looking for (?:a )?sponsor|propose.{0,40}(?:bounty|\$)", combined, re.I):
        return reject("Beloppet verkar vara ett förslag, inte ett betalningslöfte.", "skipped")
    if issue.get("authorAssociation") not in {"OWNER", "MEMBER", "COLLABORATOR"}:
        if issue.get("authorAssociation") in {None, "", "UNKNOWN"}:
            return reject("GitHub visar ingen verifierbar projektroll för den som annonserat belöningen.")
        return reject("Författarens GitHub-roll bekräftar inte ansvar för projektet.")
    if issue.get("assignedActors", {}).get("nodes") or issue.get("assignees", {}).get("nodes"):
        return reject("Uppdraget är tilldelat någon annan.")
    labels = " ".join(str(node.get("name", "")) for node in walk(issue.get("labels", {})))
    if re.search(r"claimed|reserved|in.progress|assigned", labels, re.I):
        return reject("Uppdraget är reserverat eller pågår redan.")
    if re.search(r"comment\s+[`\"']?/(?:attempt|claim)|must.{0,25}(?:claim|assigned)|first.{0,12}assigned", body, re.I):
        return reject("Uppdraget kräver en reservation innan arbete kan börja.")
    for key in ("linkedPullRequests", "closedByPullRequestsReferences"):
        if any(pr.get("state") in {"OPEN", "MERGED"} for pr in issue.get(key, {}).get("nodes", [])):
            return reject("Det finns redan en länkad leverans för uppdraget.")
    timeline = issue.get("timelineItems", {})
    if timeline.get("pageInfo", {}).get("hasNextPage") or timeline.get("pageInfo", {}).get("hasPreviousPage"):
        return reject("Diskussionen är ofullständigt inläst; ledig omfattning är inte verifierad.")
    doc_labels = {str(node.get("name", "")).casefold() for node in walk(issue.get("labels", {}))}
    if not re.search(r"\b(docs?|documentation|readme|faq|typos?|spelling)\b", title, re.I) and not doc_labels.intersection({"docs", "documentation"}):
        result["kind"] = "other"
        return reject("Första versionens arbetsagent hanterar dokumentationsuppdrag.")
    if requires_code_or_runtime(combined):
        return reject("Uppdraget kräver kod, mätningar eller körning utanför dokumentationsagentens stöd.")
    result.update(status="qualified", reason="Öppet dokumentationsuppdrag med maintainer-publicerat belopp; betalning är inte verifierad.")
    if currency == "$":
        result["reason"] += " Dollarvalutan är ospecificerad; beloppet visas som $ utan omräkning."
    return result


class Ollama:
    def __init__(self, http=None, url=None, model=None):
        self.url = url or os.environ.get("BOUNTYOS_OLLAMA_URL", "http://127.0.0.1:11434")
        self.model = model or os.environ.get("BOUNTYOS_MODEL", "qwen2.5-coder:7b")
        self.http = http or HTTP(local=True, min_interval=0)
        self.last_status = {"configured": True, "status": "unknown", "detail": "Lokal Ollama är ännu inte kontrollerad.", "model": self.model}
        self._verified = False

    def check(self):
        self.url = validate_local_url(self.url)
        if "cloud" in self.model.lower():
            raise ConnectorError("Molnmodeller är avstängda med budget 0 kr.")
        data = self.http.json(self.url + "/api/tags", timeout=3)
        models = data.get("models", [])
        if not any(m.get("name") == self.model or m.get("model") == self.model for m in models):
            raise ConnectorError(f"Den lokala modellen {self.model} är inte installerad i Ollama.")
        info = self.http.json(self.url + "/api/show", payload={"model": self.model}, timeout=5)
        if any(item.get("remote_host") or item.get("remote_model") for item in walk(info)):
            raise ConnectorError("Modellen använder en fjärrtjänst. Endast lokala modeller är tillåtna.")
        if not info.get("model_info"):
            raise ConnectorError("Modellens lokala metadata kunde inte verifieras.")
        self._verified = True
        self.last_status = {"configured": True, "status": "connected", "detail": "Lokal modell tillgänglig; inga betalda API-anrop.", "model": self.model}
        return self.last_status

    def generate(self, system, data, *, schema=None):
        # Recheck the local model before each job; cloud proxy models are rejected.
        self.check()
        try:
            generation_timeout = int(os.environ.get("BOUNTYOS_MODEL_TIMEOUT", "240"))
            if not 60 <= generation_timeout <= 3600:
                raise ValueError
        except ValueError:
            raise ConnectorError("BOUNTYOS_MODEL_TIMEOUT måste vara 60–3600 sekunder.") from None
        result = self.http.json(self.url + "/api/generate", payload={
            "model": self.model, "system": system, "prompt": json.dumps(data, ensure_ascii=False),
            "stream": False, "format": schema if schema is not None else "json", "options": {"temperature": 0.15, "num_predict": 6000, "num_ctx": 16384},
        }, timeout=generation_timeout, limit=1_000_000)
        try:
            return json.loads(result["response"])
        except (KeyError, TypeError, ValueError):
            raise ConnectorError("Den lokala modellen gav ingen giltig JSON-leverans.") from None


class GitHubSubmitter:
    """Optional real delivery. Called only when auto_submit is enabled.

    Uses one commit, a deterministic branch, and checks existing PRs before writes.
    A network failure never triggers an automatic blind repeat of publication.
    """
    def __init__(self, http=None):
        self.http = http or HTTP(min_interval=1)
        self._publish_guard = None

    @property
    def configured(self):
        return bool(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))

    def api(self, path, **kwargs):
        if kwargs.get("method") in {"POST", "PUT", "PATCH", "DELETE"} and self._publish_guard and not self._publish_guard():
            raise ConnectorError("Automatisk inlämning pausades innan nästa skrivning till GitHub.")
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ConnectorError("GitHub-token behövs för att skapa en fork och pull request. Lägg in den som miljöhemlighet.")
        if not path.startswith("/") or ".." in path or "?" in path.split("/repos/")[-1].split("?")[0]:
            raise ConnectorError("Ogiltig GitHub API-sökväg.")
        return self.http.json("https://api.github.com" + path, token=token, **kwargs)

    def check(self):
        if not self.configured:
            return {"configured": False, "status": "missing", "detail": "Offentlig sökning fungerar utan token. Automatisk inlämning behöver GH_TOKEN eller GITHUB_TOKEN."}
        user = self.api("/user")
        return {"configured": True, "status": "connected", "detail": "GitHub-autentisering verifierad.", "login": user["login"]}

    @staticmethod
    def validate_code_submission(task, context, delivery, test_report, work_plan):
        """Revalidate persisted code artifacts without running target Python."""
        from .code_worker import CodeWorker, MAX_SOURCE_CHARS, implementation_path, safe_python_path

        repo, _ = issue_parts(task["url"])
        expected_test = "tests/test_bountyos_regression_" + hashlib.sha256(task["url"].encode()).hexdigest()[:12] + ".py"
        files = context.get("files")
        commit = context.get("commit")
        if (context.get("kind") != "code" or context.get("repo") != repo
                or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit)
                or not isinstance(files, dict) or not 1 <= len(files) <= 30
                or any(not safe_python_path(path) or not isinstance(content, str)
                       or "\x00" in content for path, content in files.items())
                or len({path.casefold() for path in files}) != len(files)
                or sum(map(len, files.values())) > MAX_SOURCE_CHARS
                or context.get("regression_path") != expected_test
                or expected_test.casefold() in {path.casefold() for path in files}
                or context.get("new_files") != [expected_test]):
            raise ConnectorError("Kodleveransens versionsbundna underlag eller regressionstest är ogiltigt.")
        editable = context.get("editable_paths")
        allowed = {path for path in files if implementation_path(path)} | {expected_test}
        if (not isinstance(editable, list) or not editable
                or any(not isinstance(path, str) or path not in allowed for path in editable)
                or len(set(editable)) != len(editable) or expected_test not in editable
                or not isinstance(work_plan, dict)):
            raise ConnectorError("Kodleveransens tillåtna filer eller arbetsplan är ogiltiga.")
        plan = CodeWorker._plan({**work_plan, "feasible": True}, context)
        CodeWorker._delivery(delivery, context, plan)
        if not isinstance(test_report, dict) or test_report.get("regression_path") != expected_test:
            raise ConnectorError("Kodleveransen saknar en fullständig testrapport för regressionstestet.")
        baseline = test_report.get("baseline")
        regression = test_report.get("regression_before")
        final = test_report.get("final")

        def counts_valid(report):
            return (isinstance(report, dict)
                    and all(type(report.get(name)) is int and 0 <= report[name] <= 100000
                            for name in ("tests_run", "failure_count", "error_count", "skipped_count"))
                    and report.get("expected_failure_count", 0) == 0
                    and report.get("unexpected_success_count", 0) == 0)

        if (not all(counts_valid(report) for report in (baseline, regression, final))
                or not CodeWorker._report_ok(baseline) or not CodeWorker._report_ok(final)
                or regression.get("passed") is not False or regression["failure_count"] <= 0
                or regression["failure_count"] > regression["tests_run"]
                or regression["error_count"] != 0 or regression["skipped_count"] != 0
                or regression.get("timed_out")
                or regression["tests_run"] <= baseline["tests_run"]
                or final["tests_run"] != regression["tests_run"]):
            raise ConnectorError("Kodleveransen kräver godkänd baslinje, ett test som hittar originalfelet och en godkänd slutkörning.")
        return delivery

    def submit(self, task, context, delivery, may_publish=None, *, test_report=None, work_plan=None):
        self._publish_guard = may_publish
        code = task.get("specialist") == "code" or context.get("kind") == "code"
        if code:
            from .code_worker import safe_python_path
            self.validate_code_submission(task, context, delivery, test_report, work_plan)
            safe_path = safe_python_path
        else:
            validate_delivery(delivery)
            safe_path = safe_doc_path
        repo, number = issue_parts(task["url"])
        user = self.api("/user")["login"]
        if not re.fullmatch(r"[A-Za-z0-9-]+", user):
            raise ConnectorError("Ogiltigt GitHub-kontonamn.")
        branch = "bountyos/issue-" + str(number) + "-" + hashlib.sha256(task["url"].encode()).hexdigest()[:8]
        head = f"{user}:{branch}"
        existing = self.api(f"/repos/{repo}/pulls?" + parse.urlencode({"state": "all", "head": head}))
        if existing:
            return existing[0]["html_url"]
        upstream = self.api(f"/repos/{repo}")
        base = upstream["default_branch"]
        ref = self.api(f"/repos/{repo}/git/ref/heads/{parse.quote(base, safe='')}")
        base_sha = ref["object"]["sha"]
        if code and base_sha != context["commit"]:
            raise ConnectorError("Projektets kod har ändrats sedan testkörningen. Nytt arbete och nya tester behövs innan inlämning.")
        evidence = context.get("files", {})
        if not isinstance(evidence, dict):
            raise ConnectorError("Leveransens filunderlag är ogiltigt.")
        delivered_paths = {item["path"] for item in delivery["files"]}
        new_paths = sorted(delivered_paths.difference(evidence))
        if new_paths:
            allowed_new = context.get("new_files", [])
            snapshot = context.get("commit")
            if (not isinstance(allowed_new, list)
                    or not all(safe_path(path) for path in allowed_new)
                    or not set(new_paths).issubset(allowed_new)
                    or not isinstance(snapshot, str)
                    or not re.fullmatch(r"[0-9a-fA-F]{40}", snapshot)
                    or not isinstance(base_sha, str)
                    or not re.fullmatch(r"[0-9a-fA-F]{40}", base_sha)
                    or not evidence):
                raise ConnectorError("Nya filer kräver en uttryckligen tillåten sökväg och verifierat underlag från en bestämd commit.")
            # A contents 404 can also mean a missing ref. Prove the older
            # snapshot exists before treating its 404 responses as absent files.
            if snapshot != base_sha:
                prior = self.api(f"/repos/{repo}/git/commits/{snapshot}")
                if not isinstance(prior, dict) or prior.get("sha") != snapshot:
                    raise ConnectorError("Underlagets commit kunde inte verifieras; ny granskning behövs.")
            for path in new_paths:
                for revision in dict.fromkeys((snapshot, base_sha)):
                    try:
                        self.api(f"/repos/{repo}/contents/{parse.quote(path, safe='/')}?ref={parse.quote(revision, safe='')}")
                    except HTTPFailure as exc:
                        if exc.status_code == 404:
                            continue
                        raise
                    raise ConnectorError("Den nya filen finns redan i projektet: " + path + ". Ny granskning behövs.")
        # A snapshot mismatch requires regenerated work, not overwriting new upstream docs.
        # New documentation can rely on any evidence file, including unchanged
        # files that are not part of the eventual delivery.
        for path, text in evidence.items():
            if new_paths or path in delivered_paths:
                if not safe_path(path) or not isinstance(text, str):
                    raise ConnectorError("Leveransens filunderlag är ogiltigt.")
                item = self.api(f"/repos/{repo}/contents/{parse.quote(path, safe='/')}?ref={parse.quote(base_sha, safe='')}")
                if (not isinstance(item, dict) or item.get("type") != "file"
                        or item.get("target") is not None or item.get("submodule_git_url") is not None):
                    raise ConnectorError("Underlaget är inte längre en vanlig fil; ny granskning behövs.")
                try:
                    current = base64.b64decode("".join(item.get("content", "").split()), validate=True).decode("utf-8")
                except (ValueError, TypeError, AttributeError, UnicodeDecodeError):
                    raise ConnectorError("Underlagets innehåll kunde inte verifieras; ny granskning behövs.") from None
                if current != text:
                    raise ConnectorError("Underlaget har ändrats sedan arbetet skapades; ny granskning behövs.")
        def check_tested_head():
            if code:
                latest = self.api(f"/repos/{repo}/git/ref/heads/{parse.quote(base, safe='')}")
                if latest.get("object", {}).get("sha") != context["commit"]:
                    raise ConnectorError("Projektets kod har ändrats sedan testkörningen. Nytt arbete och nya tester behövs innan inlämning.")

        # Recheck after the evidence reads and again before opening the PR; a
        # tested code patch must never be silently rebased onto a newer head.
        check_tested_head()
        fork = self.api(f"/repos/{repo}/forks", payload={"default_branch_only": True}, method="POST")
        fork_name = fork["full_name"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", fork_name) or fork_name.split("/")[0].lower() != user.lower():
            raise ConnectorError("Forkens ägare kunde inte verifieras.")
        # Fork creation is asynchronous. Poll reads only; never repeat the POST.
        commit = None
        for attempt in range(5):
            try:
                commit = self.api(f"/repos/{fork_name}/git/commits/{base_sha}")
                break
            except HTTPFailure as exc:
                if exc.status_code not in {404, 409}:
                    raise
                if attempt == 4:
                    raise ConnectorError("Forken är ännu inte färdig. Inlämningen behöver kontrolleras innan nytt försök.") from None
                time.sleep(2 ** attempt)
        existing_ref = None
        try:
            existing_ref = self.api(f"/repos/{fork_name}/git/ref/heads/{parse.quote(branch, safe='')}")
        except HTTPFailure as exc:
            if exc.status_code != 404:
                raise
        tree = self.api(f"/repos/{fork_name}/git/trees", method="POST", payload={"base_tree": commit["tree"]["sha"], "tree": [
            {"path": item["path"], "mode": "100644", "type": "blob", "content": item["content"]} for item in delivery["files"]
        ]})
        prefix = "fix" if code else "docs"
        if existing_ref:
            prior_commit = self.api(f"/repos/{fork_name}/git/commits/{existing_ref['object']['sha']}")
            if prior_commit.get("tree", {}).get("sha") != tree["sha"] or [p.get("sha") for p in prior_commit.get("parents", [])] != [base_sha]:
                raise ConnectorError("En tidigare arbetsgren har annat innehåll. Kontrollera den innan ny inlämning.")
        else:
            new_commit = self.api(f"/repos/{fork_name}/git/commits", method="POST", payload={"message": f"{prefix}: address {repo}#{number}", "tree": tree["sha"], "parents": [base_sha]})
            self.api(f"/repos/{fork_name}/git/refs", method="POST", payload={"ref": "refs/heads/" + branch, "sha": new_commit["sha"]})
        if code:
            validation = ("Prepared with a local AI assistant and independently reviewed against the issue and pinned source code.\n\n"
                          f"Validation at commit `{base_sha}` used Python 3.12 unittest in isolated Docker containers without network access. "
                          f"Baseline: {test_report['baseline']['tests_run']} tests passed. "
                          f"Original code with the new regression: {test_report['regression_before']['tests_run']} tests ran, "
                          f"{test_report['regression_before']['failure_count']} assertion failure(s), zero errors. "
                          f"Repaired code: {test_report['final']['tests_run']} tests passed. "
                          "Existing tests were preserved. These checks do not imply bounty acceptance or payment.")
        else:
            validation = ("Prepared with a local AI assistant and reviewed against the issue and source documentation. "
                          "Documentation path and content checks passed. No project tests were executed; no claim of runtime validation is made.")
        check_tested_head()
        pr = self.api(f"/repos/{repo}/pulls", method="POST", payload={"title": (prefix + ": " + task["title"])[:200], "head": head, "base": base,
            "body": f"Closes #{number}\n\n{delivery['summary']}\n\n" + validation})
        return pr["html_url"]
