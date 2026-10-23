import base64
import hashlib
import unittest
from unittest.mock import Mock
from bountyos.connectors import GitHub, HTTPFailure, LoginRedirect, ConnectorError, safe_doc_path
from bountyos.code_worker import CodeWorker
from bountyos.github_api import documentation_context


class RepositoryAPITests(unittest.TestCase):
    repo = 'owner/project'
    sha = 'a' * 40
    tree_sha = 'b' * 40

    def setUp(self):
        self.http = Mock()
        self.http.get.side_effect = LoginRedirect('login')
        self.files = {'CONTRIBUTING.md': '# Contributing', 'doc/guide.md': '# Guide'}
        self.calls = []
        self.http.json.side_effect = self.read
        self.issue = {'repo': self.repo, 'url': 'https://github.com/owner/project/issues/7',
                      'title': 'Docs', 'body': 'Create docs/new.md.'}

    def read(self, url, **kwargs):
        self.calls.append(url)
        base = 'https://api.github.com/repos/' + self.repo
        if url == base: return {'full_name': self.repo, 'default_branch': 'main'}
        if url == base + '/commits/main': return {'sha': self.sha}
        if url == base + '/git/trees/' + self.sha + '?recursive=1':
            return {'sha': self.tree_sha, 'tree': [{'path': p, 'type': 'blob', 'mode': '100644'} for p in self.files]}
        if '/contents/' in url:
            path, query = url.split('/contents/')[1].split('?')
            self.assertEqual(query, 'ref=' + self.sha)
            if path not in self.files: raise HTTPFailure(404)
            raw = self.files[path].encode()
            return {'path': path, 'type': 'file', 'encoding': 'base64',
                    'sha': hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),
                    'content': base64.b64encode(raw).decode()}
        raise AssertionError(url)

    def test_root_login_uses_pinned_docs_and_verified_absence(self):
        context = GitHub(http=self.http).repository_context(self.issue)
        self.assertEqual(context['files'], self.files)
        self.assertEqual(context['commit'], self.sha)
        self.assertEqual(context['new_files'], ['docs/new.md'])

    def test_docs_directory_login_also_uses_api(self):
        self.http.get.side_effect = ['{"defaultBranch":"main","refInfo":{"name":"main","currentOid":"'+self.sha+'"}}', LoginRedirect('login')]
        self.assertEqual(GitHub(http=self.http).repository_context(self.issue)['files'], self.files)

    def test_file_403_cannot_authorize_new_file(self):
        original = self.read
        def fail(url, **kwargs):
            if '/contents/docs/new.md?' in url: raise HTTPFailure(403)
            return original(url, **kwargs)
        self.http.json.side_effect = fail
        with self.assertRaises(HTTPFailure): documentation_context(self.http, self.issue)

    def test_invalid_blob_and_wrong_repository_are_rejected(self):
        original = self.read
        def bad_blob(url, **kwargs):
            value = original(url, **kwargs)
            if '/contents/' in url: value['sha'] = '0' * 40
            return value
        self.http.json.side_effect = bad_blob
        with self.assertRaises(ConnectorError): documentation_context(self.http, self.issue)
        self.http.json.side_effect = None
        self.http.json.return_value = {'full_name': 'other/project', 'default_branch': 'main'}
        with self.assertRaises(ConnectorError): documentation_context(self.http, self.issue)

    def test_broader_docs_paths_keep_secrets_and_code_excluded(self):
        for path in ['CONTRIBUTING.md', 'readme.md', 'CHANGELOG.md', 'documentation/start.txt', 'doc/guide.md']:
            self.assertTrue(safe_doc_path(path), path)
        for path in ['AGENTS.md', 'AGENTS.memory.md', 'doc/credentials.md', 'docs/x.py', '../guide.md', '.github/workflows/test.md']:
            self.assertFalse(safe_doc_path(path), path)

    def test_code_context_resolves_api_commit_before_archive_download(self):
        from unittest.mock import patch
        worker = CodeWorker(GitHub(http=self.http), Mock(), Mock())
        worker._download_snapshot = Mock(return_value=b'archive')
        with patch('bountyos.code_worker.read_snapshot', return_value={'parser.py': 'def parse(): return 1', 'tests/test_parser.py': 'import unittest'}):
            context = worker.context(self.issue)
        self.assertEqual(context['commit'], self.sha)
        worker._download_snapshot.assert_called_once_with(self.repo, self.sha)
