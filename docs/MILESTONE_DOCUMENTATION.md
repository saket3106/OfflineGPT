# Evidence-gated milestone documentation

`scripts/milestone_docs.py` is a separate, opt-in documentation controller. It
reads the existing private maintenance ledger; it does not replace source sync,
modify a frozen scheduled runtime, build an executable or create a release.
Routine fixes, prompts, file counts and successful source CI remain maintenance.
They do not establish application intelligence or performance.

## Classification and acceptance

Reviewed, repository-owned `milestone-contracts.json` defines public wording,
source scope and required correctness criteria. New contracts require ordinary
source review and exact CI. The initial contracts cover isolated candidate
infrastructure and native semantic reasoning. Their existence does not certify
either milestone or trigger a README update.

| Kind | Minimum additional evidence |
| --- | --- |
| Engineering infrastructure | Actual Windows integration artifacts and independently reviewed lifecycle/preservation criteria; explicitly labeled non-native |
| Application capability | Actual packaged end-to-end evidence; AI reasoning contracts require actual native invocations and independently reviewed semantic criteria |
| Application performance | Comparable baseline/updated samples, at least three each, matching hardware/workload, separate cold/warm interpretation, accepted quality and at least the reviewed improvement threshold (minimum 15%) |
| Compatibility | Actual clean-Windows evidence and the contract's independently reviewed criteria |

The controller rechecks private repository policy, branch/remote identity,
clean worktree/index, exact current CI and historical milestone CI. A milestone
must match an existing ledger batch and accepted ancestor commit. Every scoped
source blob must match its reviewed digest and remain identical in current HEAD.
Missing/failed/duplicate checks, source CI used as runtime evidence, mock
execution, absent/altered execution artifacts, unknown inputs and stale reviews
block certification. Private titles, prompts and receipt content are never used
as public prose; only reviewed contract wording is rendered.

**Trust boundary:** hashes and schemas detect drift, not fabricated evidence by
the same local account. An independent reviewer must inspect original runtime
artifacts and assess correctness against meaningful criteria. Setting a Boolean
to true is not an independent review. The controller cannot determine arbitrary
semantic correctness or prove that a human attestation is honest. Genuine model
tests and hardware gates remain mandatory for native claims.

## Private certification schema

Keep all completed values outside the repository, under the configured source
manager's `milestones` child. Do not commit receipts or completed certifications.

An independent certification contains:

- `version: 1`, exact-byte `ledgerSha256`, and `entries` (empty is valid).
- Each entry: existing `batch`, reviewed `contract` ID, `sourceCommit`, complete
  scoped `inputs` path-to-SHA256 map, relative `proof` path and `proofSha256`.
- `reviews`: all of `independentCorrectness`, `publicContent`,
  `dependenciesAndLicenses`, `limitations` explicitly true after actual review.
- Each proof: `version`, `sourceCommit`, `inputs`, exact contract `kind`,
  `execution: actual`, actual `modelInvocations`, `checks`, `measurements`,
  `artifacts`.
- `checks` must cover every contract ID exactly once, each with `passed` and
  `independentlyReviewed`. Additional or missing checks are rejected.
- `artifacts` is a nonempty list of private relative `path` and exact `sha256`
  pairs. Preserve original receipts and chain them to authentic runtime evidence.
- `measurements` is empty except for performance contracts, where it contains
  bounded positive finite `baselineMs`/`updatedMs` arrays and reviewed
  `sameHardware`, `sameWorkload`, `qualityAccepted`, `coldWarmSeparated` flags.

Different source identities must not be relabeled as the same build. If an old
artifact has been independently associated with unchanged inputs, retain that
association and original build identity as evidence. A proof adapter needs
independent review; the controller does not invent or infer such an adapter.

## Commands and recovery

```powershell
$Config = Read-Host 'Existing private source-sync config'
$Evidence = Read-Host 'Reviewed private milestone certification'
python -B scripts/milestone_docs.py --config $Config prepare --evidence $Evidence
# Inspect the exact proposed public text and classification in the private plan.
python -B scripts/milestone_docs.py --config $Config apply --plan PLAN_ID --digest EXACT_DIGEST --reviewed-public-content
```

`prepare` writes only a private proposal. An empty certification produces
`NO_DOCUMENT_CHANGES`; CI and ledger counts alone never create a capability.
`apply` requires the exact reviewed digest, revalidates source/index/evidence,
and writes only `README.md` and `CHANGELOG.md`. A small managed marker block
retains previous milestone entries. Existing prose outside it is preserved;
duplicate milestone IDs do not create another entry. Public scope, limitations
and accepted source identity are included. Neither command stages, commits,
pushes, installs or releases anything. Refresh the source manifest and pass
ordinary actual-index publication checks before separately publishing docs.

The existing repository mutex coordinates cooperating agents. A private journal
preserves complete before/after bytes before either file changes. If interrupted:

```powershell
python -B scripts/milestone_docs.py --config $Config recover --plan PLAN_ID
```

Recovery checks unchanged HEAD/index/config, unrelated files, reviewed evidence
and rendering, remote acceptance and each complete old/new document value. An
unknown value is preserved for reconciliation; absence alone is never success.
Atomic replacement and file fsync support process-interruption recovery, not a
claim of guaranteed physical power-loss durability or a hostile-writer sandbox.

## Integration status

Both Codex and Antigravity can call this same completed-unit protocol. It has
disposable real Git/filesystem tests with explicitly synthetic receipts. A live
Antigravity session is not certified. The existing frozen source/candidate tasks
remain independent and unchanged. No unattended README writer is activated by
these commands. Scheduling a future writer requires the normal reviewed frozen
runtime upgrade and real scheduled acceptance; no editable working-tree script
should be installed as a background task. Native reasoning and performance
milestones remain unclaimed until authentic evidence passes the review gate.
