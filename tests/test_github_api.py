import json
import unittest
from unittest.mock import Mock
from bountyos.connectors import GitHub, LoginRedirect, RedirectBlocked, HTTPFailure, ConnectorError

URL = 'https://github.com/owner/project/issues/7'
API = 'https://api.github.com/repos/owner/project/issues/7'


def raw():
    return {'html_url': URL, 'number': 7, 'title': 'Docs: fix README', 'body': 'Bounty $25',
            'state': 'open', 'comments': 1, 'assignees': [], 'labels': [],
            'user': {'login': 'owner'}, 'author_association': 'OWNER'}


def comment():
    return {'event': 'commented', 'html_url': URL + '#issuecomment-12', 'body': 'Bounty withdrawn.',
            'user': {'login': 'owner'}, 'author_association': 'OWNER', 'created_at': '2026-10-23T10:00:00Z'}


class PublicAPITests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.http.get.side_effect = LoginRedirect('login')
        self.github = GitHub(http=self.http)
        self.http.json.side_effect = [raw(), [comment()]]

    def test_login_falls_back_without_credentials_and_preserves_source_metadata(self):
        result = self.github.issue(URL)
        self.assertEqual(result['read_source'], 'github_public_api')
        self.assertEqual(result['authorAssociation'], 'OWNER')
        node = result['timelineItems']['nodes'][0]
        self.assertEqual(node['body'], 'Bounty withdrawn.')
        self.assertEqual(node['url'], comment()['html_url'])
        self.assertFalse(result['timelineItems']['pageInfo']['hasNextPage'])
        self.assertEqual([c.args[0] for c in self.http.json.call_args_list], [API, API + '/timeline?per_page=100&page=1'])
        self.assertTrue(all('token' not in c.kwargs for c in self.http.json.call_args_list))

    def test_generic_redirect_is_not_followed(self):
        self.http.get.side_effect = RedirectBlocked('moved')
        with self.assertRaises(RedirectBlocked): self.github.issue(URL)
        self.http.json.assert_not_called()

    def test_incomplete_web_discussion_uses_api(self):
        self.http.get.side_effect = None
        issue = {'number': 7, 'url': URL, 'body': '', 'state': 'OPEN',
                 'timelineItems': {'pageInfo': {'hasNextPage': True}}}
        self.http.get.return_value = '<script type="application/json">' + json.dumps(issue) + '</script>'
        self.assertEqual(self.github.issue(URL)['read_source'], 'github_public_api')

    def test_pagination_finds_later_comments_and_checks_merged_pull_request(self):
        pull_url = 'https://github.com/other/project/pull/2'
        events = [{'event': 'labeled'}] * 100
        linked = {'event': 'cross-referenced', 'source': {'issue': {'html_url': pull_url, 'pull_request': {}}}}
        self.http.json.side_effect = [raw(), events, [comment(), linked], {'html_url': pull_url, 'state': 'closed', 'merged': True}]
        result = self.github.issue(URL)
        self.assertFalse(result['timelineItems']['pageInfo']['hasNextPage'])
        self.assertEqual(result['linkedPullRequests']['nodes'][0]['state'], 'MERGED')
        self.assertEqual(self.http.json.call_args_list[-1].args[0], 'https://api.github.com/repos/other/project/pulls/2')

    def test_page_cap_and_missing_comments_never_imply_complete_discussion(self):
        self.http.json.side_effect = [raw()] + [[{'event': 'labeled'}] * 100] * 6
        self.assertTrue(self.github.issue(URL)['timelineItems']['pageInfo']['hasNextPage'])
        self.http.json.side_effect = [raw(), []]
        self.assertTrue(self.github.issue(URL)['timelineItems']['pageInfo']['hasNextPage'])

    def test_wrong_issue_or_foreign_comment_rejected(self):
        data = raw(); data['html_url'] = URL.replace('/7', '/8')
        self.http.json.side_effect = [data]
        with self.assertRaises(ConnectorError): self.github.issue(URL)
        foreign = comment(); foreign['html_url'] = 'https://github.com/evil/repo/issues/7#issuecomment-12'
        self.http.json.side_effect = [raw(), [foreign]]
        with self.assertRaises(ConnectorError): self.github.issue(URL)

    def test_unavailable_api_is_distinct_from_transient_failure(self):
        self.http.json.side_effect = HTTPFailure(404)
        with self.assertRaisesRegex(RedirectBlocked, 'HTTP 404'): self.github.issue(URL)
        self.http.json.side_effect = HTTPFailure(503)
        with self.assertRaises(HTTPFailure): self.github.issue(URL)

    def test_missing_timeline_is_not_reported_as_missing_original_issue(self):
        self.http.json.side_effect = [raw(), HTTPFailure(404)]
        with self.assertRaisesRegex(ConnectorError, "kunde läsa ärendet") as caught:
            self.github.issue(URL)
        self.assertNotIsInstance(caught.exception, RedirectBlocked)
        self.assertNotIsInstance(caught.exception, HTTPFailure)

    def test_assignment_and_closed_status_are_preserved(self):
        data = raw(); data.update(state='closed', assignees=[{'login': 'worker'}], labels=[{'name': 'reserved'}])
        self.http.json.side_effect = [data, [comment()]]
        result = self.github.issue(URL)
        self.assertEqual(result['state'], 'CLOSED')
        self.assertEqual(result['assignedActors']['nodes'], data['assignees'])
        self.assertEqual(result['labels']['nodes'], data['labels'])
