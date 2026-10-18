import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('sign', Path(__file__).with_name('sign.py'))
sign = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sign)


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.repo = self.path / 'repo'
        subprocess.run(['git', 'init', '--quiet', str(self.repo)], check=True)
        self.env = dict(os.environ, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.com',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.com',
                        GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1')
        sign.run(self.repo, ['config', 'core.hooksPath', '/dev/null'], self.env)
        (self.repo / 'formatter.ts').write_text('before\n')
        sign.run(self.repo, ['add', '.'], self.env)
        sign.run(self.repo, ['commit', '-qm', 'Base'], self.env)
        self.base = sign.run(self.repo, ['rev-parse', 'HEAD'], self.env)
        (self.repo / 'formatter.ts').write_text('after\n')
        sign.run(self.repo, ['commit', '-qam', 'Fix'], self.env)
        self.head = sign.run(self.repo, ['rev-parse', 'HEAD'], self.env)
        self.key = self.path / 'key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.key)], check=True)

    def test_real_signature_and_exact_tree(self):
        commit = sign.signed_commit(self.repo, self.base, self.head, self.key,
                                    'test@example.com', 'Units: Fix', self.env)
        self.assertEqual(sign.run(self.repo, ['rev-parse', commit+'^{tree}'], self.env),
                         sign.run(self.repo, ['rev-parse', self.head+'^{tree}'], self.env))
        self.assertEqual(sign.run(self.repo, ['rev-parse', commit+'^'], self.env), self.base)
        allowed = self.path / 'allowed_signers'
        allowed.write_text('test@example.com ' + self.key.with_suffix('.pub').read_text())
        sign.run(self.repo, ['-c', 'gpg.ssh.allowedSignersFile='+str(allowed),
                            'verify-commit', commit], self.env)

    def test_refuses_workflow_changes(self):
        directory = self.repo / '.github' / 'workflows'
        directory.mkdir(parents=True)
        (directory / 'unsafe.yml').write_text('data\n')
        sign.run(self.repo, ['add', '.'], self.env)
        sign.run(self.repo, ['commit', '-qm', 'Workflow'], self.env)
        head = sign.run(self.repo, ['rev-parse', 'HEAD'], self.env)
        with self.assertRaisesRegex(ValueError, 'manual signing'):
            sign.signed_commit(self.repo, self.base, head, self.key,
                               'test@example.com', 'Fix', self.env)

    def test_force_lease_preserves_concurrent_updates(self):
        remote = self.path / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '--quiet', str(remote)], check=True)
        sign.run(self.repo, ['remote', 'add', 'origin', str(remote)], self.env)
        sign.run(self.repo, ['push', 'origin', self.head+':refs/heads/fix/test'], self.env)
        # Advance the branch after the expected head was captured.
        (self.repo / 'formatter.ts').write_text('newer work\n')
        sign.run(self.repo, ['commit', '-qam', 'Concurrent change'], self.env)
        newer = sign.run(self.repo, ['rev-parse', 'HEAD'], self.env)
        sign.run(self.repo, ['push', 'origin', newer+':refs/heads/fix/test'], self.env)
        with self.assertRaises(RuntimeError):
            sign.run(self.repo, ['push', '--force-with-lease=refs/heads/fix/test:'+self.head,
                                'origin', self.base+':refs/heads/fix/test'], self.env)
        self.assertEqual(sign.run(remote, ['rev-parse', 'refs/heads/fix/test'], self.env), newer)


class RequestTests(unittest.TestCase):
    def event(self):
        return {'repository': {'full_name': sign.REPO}, 'sender': {'login': sign.OWNER},
                'number': 3, 'action': 'labeled', 'label': {'name': sign.LABEL},
                'pull_request': {'head': {'sha': 'a'*40}}}

    def pr(self):
        return {'state': 'open', 'user': {'login': sign.OWNER},
                'head': {'sha': 'a'*40, 'ref': 'fix/test', 'repo': {'full_name': sign.REPO}},
                'base': {'sha': 'b'*40, 'repo': {'full_name': sign.REPO}}}

    def test_owner_label_request(self):
        self.assertEqual(sign.request_details('pull_request_target', self.event()), (3, 'a'*40))

    def test_non_owner_rejected(self):
        event = self.event()
        event['sender']['login'] = 'another-user'
        with self.assertRaisesRegex(ValueError, 'owner'):
            sign.request_details('pull_request_target', event)

    def test_stale_head_rejected(self):
        with self.assertRaisesRegex(ValueError, 'branch changed'):
            sign.validate_pr(self.pr(), 'c'*40)

    def test_main_and_cross_repo_rejected(self):
        pr = self.pr()
        pr['head']['ref'] = 'main'
        with self.assertRaises(ValueError):
            sign.validate_pr(pr, 'a'*40)
        pr = self.pr()
        pr['head']['repo']['full_name'] = 'other/grafana'
        with self.assertRaises(ValueError):
            sign.validate_pr(pr, 'a'*40)


if __name__ == '__main__':
    unittest.main()
