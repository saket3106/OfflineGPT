# Approved development-source synchronization

This repository is the publication copy. The active development workspace and installed application remain separate. **Do not drag/copy the whole development tree here.** The foundation `sync_source.py` never stages, commits, pushes, fetches, changes remotes or modifies repository visibility. The optional completed-batch wrapper described in [AUTO_SYNC.md](AUTO_SYNC.md) may stage, commit and push reviewed snapshots to its configured development branch. The manual foundation workflow below remains available.

## Ownership and safety model

- `scripts/source_sync_policy.json` lists **83 exact approved mirror paths**, **8 protected staging-specific copies** and **29 required files**. It does not authorize recursive copying. New source files are discovered in bounded application/frontend/test/document locations and reported as unapproved; they are never copied until explicitly reviewed and added to policy.
- `scripts/source_sync_state.json` records the last reviewed development/repository byte hashes. It has no machine-specific source paths, private file contents or credentials. Preview compares both sides to this baseline; staging-only edits and divergent changes require reconciliation, rather than overwriting one side.
- Sanitized tests, README, AGENTS instructions and curated requirements are protected from source mirroring. Repository documentation, scripts, constraints and voice/build dependency files remain manually managed. Protected-source changes are reported for manual porting; never replace a sanitized file wholesale with its development counterpart.
- Source extensions do not prove privacy. An exact allowlist, bounded file/type checks, credential/path scanning, Python/JSON syntax checks, required-file checks, dependency import review and **human inspection of exact diffs** are all needed. Heuristics cannot certify confidentiality, copyright ownership or license compliance.
- Development files are only read. An approved source deletion can remove **only its managed repository copy**, with an external backup and explicit reviewed-plan approval. Required-file deletion is blocked. Models/private data/binaries/rollback files cannot be synchronized.
- Reports, reviewed plans and transaction backups must be outside both trees. Plans contain local paths and must not be committed. Git metadata/index/history/config are fingerprinted and never written by the synchronizer.

## Routine workflow

Open PowerShell in the GitHub working folder. Use your development Python, a new source-checkout virtual environment, or another suitable Python 3.12 interpreter. Git must be available on PATH, or supply `--git` with its executable path. Do not run commands from the installed application folder.

```powershell
$Source = Read-Host 'Full path to the active OfflineGPT source folder'
$Reports = Join-Path $env:LOCALAPPDATA 'OfflineGPT-Sync-Reports'
python scripts/sync_source.py preview --source $Source --report-dir $Reports
```

### 1. Review the preview

The preview writes an external `plan.json` and `SUMMARY.json`, and prints:

- exact add/modify/delete/adopt actions and before/after hashes;
- conflicts, unsafe findings and missing required files;
- unapproved additions and protected-source changes;
- dependency changes, undeclared imports and existing licensing blockers;
- exact `approvalDigest` for the whole plan.

The plan is a snapshot, not a promise about later files. Changing source, repository files, policy, baseline, Git metadata or dependencies makes it stale. Preview again after any edits. Unapproved additions are informational and remain excluded; conflicts/findings/missing required files block applying the plan.

Inspect the exact source diff against the repository. Check newly authored code/documents for personal content even if the scanner passes. Review changed package names, sources, install scripts, lockfile integrity and licenses. Requirements changes from development require a manual merge into the curated dependency files. Dynamic imports, runtime downloads, legal compatibility and asset provenance are outside the static check's assurance.

### 2. Approve only that reviewed snapshot

After you have reviewed the displayed plan and exact content:

```powershell
$Plan = Read-Host 'Full path to the reviewed external plan.json'
$Approval = Read-Host 'Its displayed approvalDigest'
python scripts/sync_source.py apply --source $Source --report-dir $Reports --plan $Plan --approve-plan $Approval --reviewed-content --reviewed-dependencies --reviewed-licenses
```

The flags record your review; they **do not certify licenses automatically**. Do not copy them without doing the reviews. No Git stage/commit/push occurs. A no-change plan writes nothing to the repository.

Stop editing files and pause other repository operations during the short apply. The tool rechecks snapshots, uses a single-transaction lock, backups, per-file atomic replacements and an external recovery journal. It checks for working-tree and Git changes before declaring completion. This is not a multi-file filesystem transaction or a guarantee against arbitrary concurrent editors, disk corruption or actual power loss. Do not run two synchronizers, GitHub Desktop operations or development editors on the target files during apply.

### 3. Validate the synchronized source

Run focused relevant regressions first in a separate verification checkout/profile. For synchronization changes:

```powershell
$env:OFFLINEGPT_TEST_GIT = (Get-Command git).Source
python -m unittest discover -s tests -p test_source_sync.py -v
python scripts/audit_repository.py
```

The last command assumes a clean source working folder; the sync tool's preview/precommit scans Git-publication files so ignored environments/builds need not be copied or scanned. The sync tests create only disposable local repositories, never production data and never commits/pushes. A skipped test is not a pass; ensure Git is available. Run application-focused tests appropriate to the changed modules, and frontend compile/Qt checks only when UI/build inputs actually changed. Do not use a source sync as permission for model workloads or production installation.

### 4. Review and stage manually in GitHub Desktop

For manually curated documentation/tests rather than mirrored source, review those changes and refresh their manifest first:

```powershell
python scripts/sync_source.py refresh-manifest --report-dir $Reports --reviewed-content
```

This audits publication files and hashes them; it does not copy development files or certify test results/licenses. Successful source apply already refreshes the manifest.

Review the Changes list and tests. Select only the approved source/documentation unit, the new baseline state and updated manifest. **Staging is your action.** Then run:

```powershell
python scripts/sync_source.py precommit --report-dir $Reports
```

This produces a new external change summary and scans **both working-tree files and Git's index**, including a previously staged secret that is no longer in a working file. It checks required indexed files and verifies the indexed manifest against the exact staged blobs. Before you stage the updated manifest/new files, `indexedManifestMatches=false` is expected and commit readiness is blocked. Resolve all findings; do not interpret an unchanged working file as proof the index is safe.

There is no automatically installed Git hook: commits made outside this documented workflow are not intercepted. The scanner does not exhaustively audit old Git history. If a historical real credential is discovered, stop publication and review rotation/remediation separately; this synchronizer never rewrites history to hide it.

The tool reports diffs, not actual functional-test success. Review real test receipts before each commit. GitHub Desktop **Commit** is local; **Push origin** uploads. Both remain manual. Repository privacy is unchanged and was not queried/altered through the GitHub API by this integration.

## Manifest portability

`SOURCE_MANIFEST.sha256` now uses **v2**: UTF-8 text has CRLF normalized to LF; binary assets use exact bytes. This prevents Git's text normalization from creating false mismatches between Windows files and staged/checkout blobs. The manifest excludes itself, Git metadata, ignored local artifacts and the transaction lock. The development/repository three-way baseline still uses exact byte hashes for this working folder; on a new machine, reconcile an intentionally different checkout newline policy before applying changes. Never reset the baseline blindly to suppress a conflict.

## Interrupted-operation recovery

An active `.source-sync.lock` blocks a new sync and points to its external journal. Do not delete it to bypass recovery. Keep the journal and backups. Recovery preflights every target, verifies backup hashes, and refuses to overwrite changes made after interruption.

For a journal you have independently reviewed, obtain its current canonical digest and recover:

```powershell
$Journal = Read-Host 'Full path to the interrupted journal.json'
python -c "import hashlib,json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print(hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest())" $Journal
$Approval = Read-Host 'The displayed journal digest'
python scripts/sync_source.py recover --report-dir $Reports --journal $Journal --approve-plan $Approval
```

Only use journals created by this tool and retained outside the repository. A completed journal can safely clear a leftover lock after its final files are verified. A conflicting recovery remains blocked for manual comparison of current files and backups; Git metadata is never restored/reset by the tool. Backups are not automatically purged.

## Extending the allowlist

1. Review the new source file privately, including embedded fixtures, paths and dependencies.
2. Add its portable relative path to `managed`; add it to `required` only if essential.
3. Keep protected/managed sets disjoint and avoid case-colliding names.
4. Preview again. A new path with different existing repository content is blocked rather than overwritten.
5. Review tests/licenses, approve and apply the new plan.

Current discovery deliberately excludes logs, backups, environments, binaries, raw prompts and models. A future new source-package directory needs a reviewed discovery-policy extension; it will not be guessed from an arbitrary large tree. Removing a managed entry from policy does not silently delete its repository file; reconcile retirement explicitly.
