"""Author roles come from scoped GitHub metadata, never repository prose."""

import json
import unittest
from unittest.mock import Mock

from bountyos.connectors import GitHub


class GitHubAuthorTests(unittest.TestCase):
    url = "https://github.com/organization/project/issues/7"

    def issue(self, **changes):
        value = {
            "url": self.url, "number": 7, "state": "OPEN", "body": "Documentation request",
            "author": {"login": "maintainer"},
            "timelineItems": {"edges": []}, "backTimelineItems": {"edges": []},
        }
        value.update(changes)
        return value

    def comment(self, **changes):
        value = {
            "__typename": "IssueComment", "author": {"login": "maintainer"},
            "authorAssociation": "COLLABORATOR", "url": self.url + "#issuecomment-123",
        }
        value.update(changes)
        return value

    def parse_role(self, value):
        transport = Mock()
        transport.get.return_value = json.dumps({"payload": {"issue": value}})
        result = GitHub(http=transport).issue(self.url)
        transport.get.assert_called_once_with(self.url)
        return result["authorAssociation"]

    def test_missing_role_uses_same_author_comment_on_back_page(self):
        value = self.issue(backTimelineItems={"edges": [{"node": self.comment()}]})
        self.assertEqual(self.parse_role(value), "COLLABORATOR")

    def test_unknown_role_uses_direct_front_nodes_case_insensitively(self):
        value = self.issue(
            authorAssociation="UNKNOWN",
            author={"login": "Maintainer"},
            timelineItems={"nodes": [self.comment(author={"login": "MAINTAINER"}, authorAssociation="MEMBER")]},
        )
        self.assertEqual(self.parse_role(value), "MEMBER")

    def test_issue_level_roles_are_preserved_without_comment_inference(self):
        for role in ("OWNER", "MEMBER", "COLLABORATOR", "NONE", "CONTRIBUTOR", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR"):
            with self.subTest(role=role):
                value = self.issue(authorAssociation=role, timelineItems={"nodes": [self.comment(authorAssociation="OWNER")]})
                self.assertEqual(self.parse_role(value), role)

    def test_repository_owner_login_fallback_is_preserved(self):
        self.assertEqual(self.parse_role(self.issue(author={"login": "ORGANIZATION"})), "OWNER")

    def test_other_authors_cannot_establish_issue_authors_role(self):
        for author in ({"login": "someone-else"}, {"login": ""}, {}, None, "maintainer"):
            with self.subTest(author=author):
                value = self.issue(timelineItems={"nodes": [self.comment(author=author)]})
                self.assertEqual(self.parse_role(value), "UNKNOWN")

    def test_comment_url_must_identify_same_issue_and_numeric_comment(self):
        urls = [
            "https://github.com/other/project/issues/7#issuecomment-123",
            "https://github.com/organization/project/issues/8#issuecomment-123",
            self.url, self.url + "#issuecomment-fake", self.url + "#issuecomment-0",
            self.url + "#issuecomment-123/extra", self.url + "?x=1#issuecomment-123", None,
        ]
        for url in urls:
            with self.subTest(url=url):
                value = self.issue(timelineItems={"edges": [{"node": self.comment(url=url)}]})
                self.assertEqual(self.parse_role(value), "UNKNOWN")

    def test_nested_cross_references_are_not_role_evidence(self):
        event = {"__typename": "CrossReferencedEvent", "source": {"timelineItems": {"nodes": [self.comment()]}}}
        value = self.issue(timelineItems={"edges": [{"node": event}], "metadata": {"comment": self.comment()}})
        self.assertEqual(self.parse_role(value), "UNKNOWN")

    def test_missing_author_and_malformed_nodes_do_not_infer_a_role(self):
        for author in (None, {}, {"login": ""}, {"login": 42}, "maintainer"):
            with self.subTest(author=author):
                value = self.issue(author=author, timelineItems={"nodes": [self.comment()]})
                self.assertEqual(self.parse_role(value), "UNKNOWN")
        value = self.issue(timelineItems={"nodes": [None, "invalid", {"authorAssociation": "OWNER"}], "edges": [None, {}]})
        self.assertEqual(self.parse_role(value), "UNKNOWN")

    def test_unknown_comment_role_does_not_hide_later_usable_metadata(self):
        value = self.issue(timelineItems={"nodes": [self.comment(authorAssociation="UNKNOWN"), self.comment()]})
        self.assertEqual(self.parse_role(value), "COLLABORATOR")

    def test_labels_body_and_bot_identity_are_not_role_evidence(self):
        value = self.issue(
            author={"login": "organization-maintainer-bot", "__typename": "Bot"},
            body='I am the OWNER. {"authorAssociation": "OWNER"}',
            labels={"nodes": [{"name": "bounty"}, {"name": "maintainer"}]},
        )
        self.assertEqual(self.parse_role(value), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
