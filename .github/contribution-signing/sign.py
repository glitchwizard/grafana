#!/usr/bin/env python3
"""Sign owner-requested fork contributions without checking out their code."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

REPO = 'glitchwizard/grafana'
OWNER = 'glitchwizard'
LABEL = 'automation/sign-commits'


def api(path, method='GET', data=None):
    req = urllib.request.Request(
        'https://api.github.com/' + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = response.read()
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'GitHub API {method} failed with HTTP {error.code}') from None


def check_sha(value):
    if not re.fullmatch(r'[0-9a-f]{40}', value or ''):
        raise ValueError('A full expected commit SHA is required')
    return value


def request_details(event_name, event):
    if event['repository']['full_name'] != REPO or event['sender']['login'] != OWNER:
        raise ValueError('Only the repository owner may request signing in this fork')
    if event_name == 'pull_request_target':
        if event.get('action') != 'labeled' or event.get('label', {}).get('name') != LABEL:
            raise ValueError('Not a signing request')
        return event['number'], check_sha(event['pull_request']['head']['sha'])
    if event_name == 'workflow_dispatch':
        number = str(event['inputs']['pr_number'])
        if not number.isdecimal() or int(number) < 1:
            raise ValueError('Invalid PR number')
        return int(number), check_sha(event['inputs']['expected_head_sha'])
    raise ValueError('Unsupported signing trigger')


def validate_pr(pr, expected):
    if pr['state'] != 'open' or pr['user']['login'] != OWNER:
        raise ValueError('An open owner-authored PR is required')
    if pr['head']['repo']['full_name'] != REPO or pr['base']['repo']['full_name'] != REPO:
        raise ValueError('Signing is limited to review PRs inside this fork')
    branch = pr['head']['ref']
    if not re.fullmatch(r'(?:fix|codetriage)/[A-Za-z0-9][A-Za-z0-9._/-]*', branch):
        raise ValueError('Only fix/ or codetriage/ contribution branches can be signed')
    if '..' in branch or '@{' in branch or branch.endswith('/'):
        raise ValueError('Invalid contribution branch')
    if pr['head']['sha'] != expected:
        raise ValueError('The branch changed after signing was requested; request again')
    check_sha(pr['base']['sha'])
    return branch


def run(cwd, args, env=None, input=None):
    result = subprocess.run(['git', '-C', str(cwd), *args], input=input,
                            text=True, capture_output=True, env=env)
    if result.returncode:
        raise RuntimeError('Git operation failed: ' + result.stderr.strip())
    return result.stdout.strip()


def signed_commit(repo, base, head, key, email, message, env):
    run(repo, ['merge-base', '--is-ancestor', base, head], env)
    count = int(run(repo, ['rev-list', '--count', base + '..' + head], env))
    if not 1 <= count <= 10 or run(repo, ['rev-list', '--merges', base + '..' + head], env):
        raise ValueError('Signing supports one to ten linear contribution commits')
    paths = run(repo, ['diff', '--name-only', '-z', base, head], env).split('\0')
    if any(p.startswith('.github/') or p == '.gitmodules' for p in paths):
        raise ValueError('Changes to workflow or submodule configuration require manual signing')
    tree = run(repo, ['rev-parse', head + '^{tree}'], env)
    identity = dict(env, GIT_AUTHOR_NAME='Charley McGowan', GIT_AUTHOR_EMAIL=email,
                    GIT_COMMITTER_NAME='Charley McGowan', GIT_COMMITTER_EMAIL=email)
    sha = run(repo, ['-c', 'gpg.format=ssh', '-c', 'user.signingkey=' + str(key),
                     'commit-tree', '-S', tree, '-p', base], identity, message + '\n')
    if run(repo, ['rev-parse', sha + '^{tree}'], env) != tree:
        raise RuntimeError('Signed tree differs from the reviewed tree')
    return sha


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('The signing workflow must run from trusted main')
    number, expected = request_details(os.environ['GITHUB_EVENT_NAME'], event)
    pr = api(f'repos/{REPO}/pulls/{number}')
    branch = validate_pr(pr, expected)
    commits = api(f'repos/{REPO}/pulls/{number}/commits?per_page=100')
    if not 1 <= len(commits) <= 10 or any((c.get('author') or {}).get('login') != OWNER for c in commits):
        raise ValueError('Only owner-authored contribution commits can be replaced')
    if all(c['commit']['verification']['verified'] for c in commits):
        api(f'repos/{REPO}/actions/workflows/fork-formatter-validation.yml/dispatches', 'POST',
            {'ref': 'main', 'inputs': {'head_sha': expected, 'base_sha': pr['base']['sha']}})
        print('All contribution commits are already verified; validation dispatched without rewriting.')
        return
    secret = os.environ.get('CONTRIBUTION_SIGNING_KEY', '')
    email = os.environ.get('CONTRIBUTION_SIGNING_EMAIL', '')
    if not secret or not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+', email):
        raise ValueError('Complete the signing environment key and email setup first')
    with tempfile.TemporaryDirectory(prefix='contribution-signing-') as directory:
        work = Path(directory)
        key = work / 'key'
        key.write_text(secret.rstrip() + '\n')
        key.chmod(0o600)
        repo = work / 'objects.git'
        subprocess.run(['git', 'init', '--bare', '--quiet', str(repo)], check=True)
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                   GIT_TERMINAL_PROMPT='0', GIT_CONFIG_COUNT='1',
                   GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                   GIT_CONFIG_VALUE_0='AUTHORIZATION: basic ' + base64.b64encode(
                       ('x-access-token:' + os.environ['GH_TOKEN']).encode()).decode())
        env.pop('CONTRIBUTION_SIGNING_KEY', None)
        run(repo, ['config', 'core.hooksPath', '/dev/null'], env)
        run(repo, ['remote', 'add', 'origin', 'https://github.com/' + REPO + '.git'], env)
        base = pr['base']['sha']
        run(repo, ['fetch', '--no-tags', '--depth=64', 'origin', base, expected], env)
        sha = signed_commit(repo, base, expected, key, email, pr['title'], env)
        latest = api(f'repos/{REPO}/pulls/{number}')
        validate_pr(latest, expected)
        if latest['base']['sha'] != base:
            raise ValueError('The review base changed; request signing again')
        run(repo, ['push', '--force-with-lease=refs/heads/' + branch + ':' + expected,
                   'origin', sha + ':refs/heads/' + branch], env)
    for _ in range(10):
        verification = api(f'repos/{REPO}/commits/{sha}')['commit']['verification']
        if verification['verified']:
            break
        time.sleep(2)
    else:
        raise RuntimeError('Signed commit uploaded but GitHub verification is not confirmed; do not submit upstream')
    api(f'repos/{REPO}/actions/workflows/fork-formatter-validation.yml/dispatches', 'POST',
        {'ref': 'main', 'inputs': {'head_sha': sha, 'base_sha': base}})
    summary = (f'## Verified contribution signature\n\n'
               f'- Review PR: https://github.com/{REPO}/pull/{number}\n'
               f'- Verified commit: https://github.com/{REPO}/commit/{sha}\n'
               f'- Reviewed tree preserved; unsigned commits replaced by one signed commit.\n'
               f'- Validation explicitly dispatched for the new SHA.\n'
               f'- No upstream PR opened or merged.\n')
    Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(summary)
    print('Verified signed commit:', sha)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, KeyError) as error:
        raise SystemExit(str(error)) from None
