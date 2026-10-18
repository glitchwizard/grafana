# Cloud contribution signing

This fork-only setup lets the existing CodeTriage task request verified signatures without storing a private key in chat, a checkout, or the task's temporary workspace. It does not submit or merge upstream PRs.

## One-time enrollment

Requirements: Python 3, `ssh-keygen`, and GitHub CLI (`gh`) authenticated as `glitchwizard`. Your GitHub authorization must permit SSH signing-key registration and administration of this fork's environment secrets and variables. This script does not change authorization scopes or account connections.

1. Review `setup.py` and `sign.py` in this directory.
2. From a local clone of this fork's `main`, run:

   ```sh
   python3 .github/contribution-signing/setup.py
   ```

3. The setup script creates a dedicated, unattended SSH signing key. It registers only the public half as a **signing key**, not an SSH authentication key.
4. It restricts the `contribution-signing` environment to `main`, stores the private half in the environment's encrypted `CONTRIBUTION_SIGNING_KEY` secret, and sets the account's numeric-ID noreply address in `CONTRIBUTION_SIGNING_EMAIL`.
5. On success, it confirms that the secret exists and deletes its temporary local key files. It never prints the private key. No upstream repository or existing connection permissions are modified.

If GitHub CLI lacks SSH signing-key permission, use GitHub's normal authorization flow for `write:ssh_signing_key`, or enroll the key through account settings yourself. Do not paste private keys into chat. If enrollment fails after public-key registration but before secret storage, the unused public entry can be removed from account settings before retrying.

## Request signing

After setup, add `automation/sign-commits` to an open review PR inside this fork, using the `glitchwizard` account. For an existing request, remove and re-add the label to retry. The scheduled task can do this through its existing label tool, without a browser session.

Alternatively, use **Actions > Sign contribution commits > Run workflow**, on `main`, supplying the review PR number and its exact current head SHA.

The job:

- Runs trusted signing code from `main` in the main-only signing environment.
- Accepts only owner-authored review PRs and `fix/` or `codetriage/` branches in `glitchwizard/grafana`.
- Fetches Git objects into a temporary bare repository. It never checks out, builds, imports, or runs contribution code. Hooks are disabled.
- Rejects changed workflow/submodule configuration, merges, more than ten contribution commits, stale requests, and concurrent branch updates.
- Replaces the contribution commits with one signed commit containing exactly the same final file tree and the same review-base parent.
- Pushes only the requested contribution branch using an explicit force-with-lease. This changes commit IDs while preserving reviewed files.
- Confirms GitHub reports the commit as **Verified**, then explicitly dispatches validation for the new head and base SHAs. This avoids relying on workflow-generated pushes to trigger more workflows.
- Records the verified commit link in the run summary. It does not open upstream PRs, merge PRs, or accept a CLA.

## Verify activation

Enrollment is not complete until the key is registered and the encrypted secret exists. End-to-end operation is not confirmed until a real signing run produces a GitHub-verified commit and the new head's validation passes. Local tests use disposable test keys and cannot establish those account-side facts.

## Test the implementation

```sh
python3 -m unittest discover -s .github/contribution-signing -p 'test_*.py' -v
```

The tests exercise real SSH signature verification and tree preservation, rejection of workflow changes, stale requests, non-owner/cross-repository requests, and a real concurrent-update lease failure.

The installed validation workflow currently covers the formatter change in review PR #3. Future issues need appropriate targeted validation configured before relying on this signing handoff; the formatter suite is not universal coverage for other changes.
