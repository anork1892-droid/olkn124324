"""Bounded public REST fallback for issue pages hidden behind GitHub login."""
from .connectors import ConnectorError, HTTPFailure, RedirectBlocked, issue_parts
import re


def read_issue(http, url):
    repo, number = issue_parts(url)
    endpoint = f"https://api.github.com/repos/{repo}/issues/{number}"
    try:
        raw = http.json(endpoint, limit=1_000_000)
    except HTTPFailure as exc:
        if exc.status_code == 404:
            raise RedirectBlocked("Ärendet kan inte läsas offentligt via GitHub API (HTTP 404). Det kan vara privat, flyttat eller borttaget. Kontrollera Visa på GitHub.") from None
        raise
    if (not isinstance(raw, dict) or raw.get("html_url") != url
            or type(raw.get("number")) is not int or raw["number"] != number
            or "pull_request" in raw or raw.get("state") not in {"open", "closed"}
            or not isinstance(raw.get("title"), str)
            or raw.get("body") is not None and not isinstance(raw.get("body"), str)
            or type(raw.get("comments")) is not int or raw["comments"] < 0
            or not isinstance(raw.get("assignees"), list) or not isinstance(raw.get("labels"), list)):
        raise ConnectorError("GitHub API kunde inte verifiera originalärendets identitet och status.")
    def related_json(address, limit):
        try:
            return http.json(address, limit=limit)
        except HTTPFailure as exc:
            if exc.status_code == 404:
                raise ConnectorError("GitHub API kunde läsa ärendet men inte hela diskussionen eller en länkad leverans. Ett nytt läsförsök behövs.") from None
            raise

    comments, pulls = [], {}
    complete = False
    comment_pattern = re.compile(re.escape(url) + r"#issuecomment-[1-9][0-9]*$")
    for page in range(1, 7):
        events = related_json(endpoint + f"/timeline?per_page=100&page={page}", limit=2_000_000)
        if not isinstance(events, list) or len(events) > 100:
            raise ConnectorError("GitHub API returnerade en ogiltig diskussion.")
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("event"), str):
                raise ConnectorError("GitHub API returnerade en ofullständig diskussionshändelse.")
            if event["event"] == "commented":
                if (not isinstance(event.get("html_url"), str) or not comment_pattern.fullmatch(event["html_url"])
                        or not isinstance(event.get("body"), str)):
                    raise ConnectorError("En kommentar kunde inte kopplas till originalärendet.")
                comments.append({"__typename": "IssueComment", "url": event["html_url"],
                                 "body": event["body"], "author": event.get("user") or {},
                                 "authorAssociation": event.get("author_association", "UNKNOWN"),
                                 "createdAt": event.get("created_at", ""), "isMinimized": bool(event.get("minimized"))})
            if event["event"] == "cross-referenced":
                source = event.get("source")
                linked = source.get("issue") if isinstance(source, dict) else None
                if not isinstance(linked, dict):
                    raise ConnectorError("En länkad leverans i diskussionen kunde inte kontrolleras.")
                if "pull_request" in linked:
                    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)", str(linked.get("html_url", "")))
                    if not match:
                        raise ConnectorError("En länkad leverans har en okänd adress.")
                    pulls[linked["html_url"]] = match.groups()
        if len(events) < 100:
            complete = True
            break
    # A truncated timeline must never be represented as fully checked.
    complete = complete and len({c["url"] for c in comments}) >= raw["comments"] and len(pulls) <= 10
    references = []
    for pull_url, (pull_repo, pull_number) in list(pulls.items())[:10]:
        pull = related_json(f"https://api.github.com/repos/{pull_repo}/pulls/{pull_number}", limit=1_000_000)
        if (not isinstance(pull, dict) or pull.get("html_url") != pull_url
                or pull.get("state") not in {"open", "closed"} or type(pull.get("merged")) is not bool):
            raise ConnectorError("GitHub API kunde inte verifiera en länkad leverans.")
        references.append({"url": pull_url, "state": "MERGED" if pull["merged"] else pull["state"].upper()})
    if any(not isinstance(x, dict) or not isinstance(x.get("login"), str) for x in raw["assignees"]):
        raise ConnectorError("GitHub API kunde inte verifiera tilldelningen.")
    if any(not isinstance(x, dict) or not isinstance(x.get("name"), str) for x in raw["labels"]):
        raise ConnectorError("GitHub API kunde inte verifiera etiketter.")
    return {"url": url, "repo": repo, "number": number, "title": raw["title"], "body": raw.get("body") or "",
            "state": raw["state"].upper(), "author": raw.get("user") or {},
            "authorAssociation": raw.get("author_association", "UNKNOWN"),
            "assignedActors": {"nodes": raw["assignees"]}, "labels": {"nodes": raw["labels"]},
            "linkedPullRequests": {"nodes": references},
            "timelineItems": {"nodes": comments, "pageInfo": {"hasNextPage": not complete, "hasPreviousPage": False}},
            "read_source": "github_public_api"}
