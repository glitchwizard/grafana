#!/usr/bin/env python3
"""Enroll a dedicated signing-only key without printing or retaining its private half."""
import json
import subprocess
import tempfile
from pathlib import Path

REPO = 'glitchwizard/grafana'
ENVIRONMENT = 'contribution-signing'


def gh(*args, input=None):
    result = subprocess.run(['gh', *args], input=input, text=True, capture_output=True)
    if result.returncode:
        raise SystemExit(result.stderr.strip() or 'GitHub CLI operation failed')
    return result.stdout


def api(path, method='GET', data=None):
    args = ['api', path, '--method', method]
    if data is not None:
        args += ['--input', '-']
    output = gh(*args, input=None if data is None else json.dumps(data))
    return json.loads(output) if output.strip() else None


def main():
    user = api('user')
    if user['login'] != 'glitchwizard':
        raise SystemExit('Authenticate gh as glitchwizard before running this script.')
    existing = json.loads(gh('secret', 'list', '--repo', REPO, '--env', ENVIRONMENT,
                             '--json', 'name')) if environment_exists() else []
    if any(s['name'] == 'CONTRIBUTION_SIGNING_KEY' for s in existing):
        raise SystemExit('Signing key already enrolled. No key was changed; use the existing setup.')
    path = f'repos/{REPO}/environments/{ENVIRONMENT}'
    if not environment_exists():
        api(path, 'PUT', {'deployment_branch_policy':
                          {'protected_branches': False, 'custom_branch_policies': True}})
        api(path + '/deployment-branch-policies', 'POST', {'name': 'main', 'type': 'branch'})
    environment = api(path)
    policy = environment.get('deployment_branch_policy') or {}
    branches = api(path + '/deployment-branch-policies')['branch_policies']
    if policy != {'protected_branches': False, 'custom_branch_policies': True} or \
       len(branches) != 1 or branches[0]['name'] != 'main' or branches[0].get('type', 'branch') != 'branch':
        raise SystemExit('Signing environment must allow only the main branch; existing rules were not changed.')
    email = f"{user['id']}+{user['login']}@users.noreply.github.com"
    with tempfile.TemporaryDirectory(prefix='github-signing-enrollment-') as directory:
        key = Path(directory) / 'signing-key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C',
                        'CodeTriage Grafana cloud signing', '-f', str(key)], check=True)
        registered = api('user/ssh_signing_keys', 'POST',
                         {'title': 'CodeTriage Grafana cloud signing',
                          'key': key.with_suffix('.pub').read_text().strip()})
        gh('variable', 'set', 'CONTRIBUTION_SIGNING_EMAIL', '--repo', REPO,
           '--env', ENVIRONMENT, '--body', email)
        gh('secret', 'set', 'CONTRIBUTION_SIGNING_KEY', '--repo', REPO,
           '--env', ENVIRONMENT, input=key.read_text())
    names = json.loads(gh('secret', 'list', '--repo', REPO, '--env', ENVIRONMENT, '--json', 'name'))
    if not any(s['name'] == 'CONTRIBUTION_SIGNING_KEY' for s in names):
        raise SystemExit('Secret storage was not confirmed; signing is not ready.')
    print('Registered signing-only public key:', registered['id'])
    print('Private key stored in the main-only signing environment; temporary local copy removed.')
    print('Next: add automation/sign-commits to review PR #3, then verify the Actions run.')


def environment_exists():
    result = subprocess.run(['gh', 'api', f'repos/{REPO}/environments/{ENVIRONMENT}'],
                            text=True, capture_output=True)
    if result.returncode == 0:
        return True
    if 'HTTP 404' in result.stderr:
        return False
    raise SystemExit(result.stderr.strip())


if __name__ == '__main__':
    main()
