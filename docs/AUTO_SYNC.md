# Completed-batch Auto-Sync

This optional wrapper extends `scripts/sync_source.py`; it does not replace its
publication, conflict, manifest or actual-index checks. It publishes source to
one explicitly configured `codex/` development branch. It never promotes main,
changes visibility, force-pushes, installs applications or uploads binaries.

## Completion protocol for both development agents

1. Complete one coherent engineering unit in the authoritative source workspace.
2. Review exact changes, dependencies, source ownership/licensing and privacy.
   New files require classification in the existing exact-path policy.
3. With a clean publication repository on the configured branch, run `prepare`.
   This checks stability, captures private immutable inputs, creates the existing
   exact preview, and tests a publication projection with sanitized files intact.
4. Review the resulting snapshot, preview, test log and acceptance evidence.
   Then `accept` that exact batch and digest, acknowledging completion and review.
5. `run-once` consumes accepted work. An optional user-level scheduled runner can
   invoke this fixed command; it cannot invent or accept tasks itself.

Stable-write detection is only a preliminary check. Unaccepted work is never
published, regardless of how long the files remain unchanged. Completion review
is an accountable agent/user attestation, not a mathematical proof of semantics.
Repository instructions provide integration; there is no invented Codex or
Antigravity completion hook. Agent sessions must actually follow this protocol.

## Private configuration

Keep configuration, snapshots, logs and credentials out of Git. Use absolute
local paths and an external private state directory. Example schema (replace
placeholders locally; do not commit the completed configuration):

```json
{
  "version": 1,
  "source": "<absolute development source>",
  "repository": "<absolute publication repository>",
  "stateRoot": "<absolute private state directory>",
  "python": "<absolute approved Python executable>",
  "git": "<absolute approved Git executable>",
  "branch": "codex/development-sync",
  "remoteUrl": "https://github.com/OWNER/REPOSITORY.git",
  "debounceSeconds": 1,
  "testProfile": "unit"
}
```

The config itself must live inside `stateRoot`. Roots must be disjoint and have
no reparse/link ancestors. The repository mutex must be ignored. Existing Git
authentication and author configuration are used. No token is stored by the
manager; CI authentication uses the existing credential helper in memory.
Prefer repository-scoped credentials. The manager cannot narrow permissions of
an already configured credential or certify the repository's visibility.

```powershell
$Config = Read-Host 'Private Auto-Sync configuration path'
python scripts/auto_sync.py --config $Config status
python scripts/auto_sync.py --config $Config prepare --title 'Verified history maintenance'
# Review the exact returned batch, snapshot digest, preview and real test log.
python scripts/auto_sync.py --config $Config accept --batch BATCH_ID --snapshot SNAPSHOT_DIGEST --reviewed-complete --reviewed-content --reviewed-dependencies --reviewed-source-licenses
python scripts/auto_sync.py --config $Config run-once
python scripts/auto_sync.py --config $Config pause
python scripts/auto_sync.py --config $Config start
python scripts/auto_sync.py --config $Config stop
```

The only test profiles are fixed standard-library unittest commands: `unit`
(full source suite) and `publication` (sync-controller suite). No arbitrary shell,
generated Python/SQL, model benchmark or release script command is accepted.
Documentation-only batches use the focused publication suite; application or
test changes require the full `unit` profile. Test execution is bounded to
180 seconds and Git commands to 30 seconds.
Private test logs may contain diagnostic fixture content and must not be synced.

## Verified publication and recovery boundaries

- A repository-wide OS mutex coordinates separate agent processes and is
  automatically released on process exit. Phase receipts survive a crash.
- Source, snapshot, config, repository, branch, remote, index and HEAD changes
  invalidate acceptance. Sanitized protected files remain repository-owned.
- Incidental Git fetch/object bookkeeping can be rebound to a fresh foundation
  preview only when its entire semantic projection, actual index, HEAD, local
  Git configuration and all source/repository bytes still match the review.
- Existing sync journals/backups coordinate filesystem updates. Only exact
  reviewed managed paths, baseline state and manifest enter the Git index.
- The actual index must match the reviewed projection and manifest. No unrelated
  staged edits can join the commit. Active/custom hooks require reconciliation.
- Remote identity and concurrent changes are checked; ordinary non-force push
  is used. Lost push acknowledgements are reconciled against the exact remote
  commit, without creating duplicate commits.
- Network failures retry with backoff, at most eight times. Conflicts, integrity
  failures and ambiguous local mutations block rather than retrying blindly.
- Interrupted APPLYING/STAGING/COMMITTING phases require retained-journal and Git
  tree review. Never infer successful deletion/commit from missing files alone.
- Only a matching push-triggered `source-safety.yml` run on the exact branch/SHA
  with successful conclusion marks source publication COMPLETE. CI failure is
  retained, not overridden. No automatic branch promotion is implemented.
- Every transition has an atomic private event receipt. Completed maintenance
  batches enter `CHANGE_LEDGER.json`; README milestone claims are not generated
  from agent descriptions or file counts.

The Windows CI runs source-publication and disposable local-Git tests only. It
does not establish model intelligence, frontend behavior, native compatibility,
installer readiness or full application release acceptance.

## Current limits

Candidate build orchestration is a separate queued workstream; the manager
records its pending state and does not pretend an EXE was rebuilt. Full native
provenance and redistribution licensing still block downloadable binaries.
Milestone documentation requires additional independent acceptance evidence.
Power-loss durability, arbitrary malicious concurrent writers and an OS-enforced
sandbox for all test subprocesses are not certified. Review any test timeout for
owned leftover processes before continuing. Private receipts are tamper-evident
through recorded hashes, not signed against a compromised local user account.

Codex continuation uses the configured desktop heartbeat, separately from the
source-publication runner. Local scheduled work needs the computer awake and
Codex running. Actual account limits/reset times govern continuation; there is
no quota bypass or unrestricted background coding agent.
