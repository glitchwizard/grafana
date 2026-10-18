import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('setup_signing', Path(__file__).with_name('setup.py'))
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class EnrollmentTests(unittest.TestCase):
    def test_wrong_account_is_rejected_before_changes(self):
        with patch.object(setup, 'api', return_value={'login': 'someone-else'}) as api:
            with self.assertRaisesRegex(SystemExit, 'glitchwizard'):
                setup.main()
            self.assertEqual(api.call_count, 1)

    def test_existing_key_is_not_overwritten(self):
        with patch.object(setup, 'api', return_value={'login': 'glitchwizard', 'id': 40527412}), \
             patch.object(setup, 'environment_exists', return_value=True), \
             patch.object(setup, 'gh', return_value='[{"name":"CONTRIBUTION_SIGNING_KEY"}]') as gh:
            with self.assertRaisesRegex(SystemExit, 'already enrolled'):
                setup.main()
            self.assertEqual(gh.call_count, 1)

    def test_private_key_is_uploaded_only_via_stdin_and_not_printed(self):
        uploaded = []
        lists = iter(['[]', '[{"name":"CONTRIBUTION_SIGNING_KEY"}]'])
        def gh(*args, input=None):
            if args[:2] == ('secret', 'list'):
                return next(lists)
            if args[:2] == ('secret', 'set'):
                self.assertTrue(input.startswith('-----BEGIN OPENSSH PRIVATE KEY-----'))
                self.assertNotIn(input, args)
                uploaded.append(input)
            return ''
        def api(path, method='GET', data=None):
            if path == 'user':
                return {'login': 'glitchwizard', 'id': 40527412}
            if path.endswith('/deployment-branch-policies'):
                return {'branch_policies': [{'name': 'main', 'type': 'branch'}]}
            if path == 'user/ssh_signing_keys':
                self.assertTrue(data['key'].startswith('ssh-ed25519 '))
                return {'id': 123}
            return {'deployment_branch_policy': {'protected_branches': False,
                                                  'custom_branch_policies': True}}
        output = io.StringIO()
        with patch.object(setup, 'api', side_effect=api), \
             patch.object(setup, 'gh', side_effect=gh), \
             patch.object(setup, 'environment_exists', return_value=True), \
             contextlib.redirect_stdout(output):
            setup.main()
        self.assertEqual(len(uploaded), 1)
        self.assertNotIn(uploaded[0], output.getvalue())
        self.assertNotIn('BEGIN OPENSSH PRIVATE KEY', output.getvalue())


if __name__ == '__main__':
    unittest.main()
