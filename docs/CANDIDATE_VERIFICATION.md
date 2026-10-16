# Isolated candidate verification

The candidate and installed application are separate artifacts. Source-only
controller updates do not justify rebuilding unchanged application inputs.
Keep previous candidate directories and production data intact.

## Build input and payload association

`scripts/candidate_inputs.py` reads an exact Git revision, not mutable working
files. It identifies the application modules, frontend source, lockfile, build
specification and system prompt through the reviewed publication policy. Text
normalization follows source publication; binary assets retain their bytes.

For an existing candidate, supply a separately reviewed full COLLECT payload
manifest and its approved SHA-256. The verifier hashes every listed file,
rejects traversal, links, missing/changed dependencies and Windows name
collisions, then compares embedded application code with the exact Git source.
Only compiled source filenames are ignored; instructions, constants, function
signatures, names, line tables and exception tables must match.

```powershell
python scripts/candidate_inputs.py --repo REPOSITORY --commit FULL_COMMIT `
  --git GIT_EXE --package CANDIDATE --manifest PRIVATE_PAYLOAD_MANIFEST `
  --manifest-sha256 APPROVED_MANIFEST_SHA --output NEW_PRIVATE_RECEIPT
```

This associates application code and a previously reviewed payload. It does not
prove that the frontend was regenerated from the identified inputs, that an
arbitrary supplied manifest includes every transitive dependency, or that
native artifacts are reproducible/redistributable. Preserve the original build
receipt, dependency closure and frontend build evidence separately. Do not
relabel an old EXE as a newly produced exact-revision build.

## Real packaged shell smoke

```powershell
python scripts/candidate_smoke.py --package CANDIDATE `
  --sha256 APPROVED_EXE_SHA --profile NEW_PRIVATE_EMPTY_PROFILE
```

The controller opens the actual EXE with a fresh disjoint synthetic profile,
blank local model paths, software rendering and temporary loopback-only Qt
DevTools. It verifies the real frontend, existing Qt bridge, composer, SQLite
integrity, normal shutdown and observed owned-process release. It does not
confirm dialogs or issue chat, vision, generation, microphone or TTS requests.
An existing user app/model causes deferral. RAM and duration limits stop the
owned test safely; cleanup checks PID creation times before terminating a hung
process. The generated receipt and profile remain private, outside Git.

The bridge lookup depends on the current React structure. If that changes, fail
and update the independently reviewed probe rather than treating startup alone
as acceptance. A shell pass is not a native inference or production-release pass.

## Private build environment

`scripts/build_environment.py` captures a private fingerprint of the existing
isolated Python venv, its matching base stdlib/runtime, cached frontend
dependencies and Python/Node executables. It installs nothing. It requires
global site packages to be disabled, excludes unrelated base-global packages,
rejects escaping links and model weights, detects changed files/topology and
uses at most four hashing workers, 64 queued files, 80,000 files, 4 GiB and
90 seconds. Transient caches/bytecode are explicitly excluded.

```powershell
python -I scripts/build_environment.py capture --python-env EXISTING_VENV `
  --python-base MATCHING_BASE --frontend-deps EXISTING_SOURCE_NODE_MODULES `
  --python APPROVED_PYTHON --node APPROVED_NODE --source ACTIVE_SOURCE `
  --repository REPOSITORY --output NEW_PRIVATE_ENVIRONMENT_RECEIPT
python -I scripts/build_environment.py verify --receipt PRIVATE_RECEIPT `
  --sha256 REVIEWED_RECEIPT_SHA
```

This records observed inputs; it is not a signed lock or proof of the complete
Python import graph. In particular, `.pth` behavior, ignored bytecode/cache use,
private dependency materialization and frontend build derivation still require
review in the execution adapter. The builder must not execute excluded caches
or silently accept changed environments. Keep receipt paths/hashes private and
never publish the machine-specific receipt itself.

## Persistent isolated executor

`scripts/candidate_builder.py` consumes completed CI-accepted source batches.
It does not run inside the source-publication lock. A separate private queue
records exact commits, application/dependency declaration hashes, reviewed
adapter hashes, environment identity, attempts, phase events and acceptance.
Identical inputs coalesce with every accepted source identity retained. Distinct
required revisions remain queued; only explicitly optional pending jobs can be
superseded. Documentation changes alone cannot create a new build identity.

The private JSON profile binds `syncConfig`, `privateRoot`, `controllerCommit`,
`environmentReceipt`, `environmentReceiptSha256`, `executableInputs`,
`allowedPth`, `systemInputs`, `baselinePackage` and `baselineExeSha256`, with
`version: 1`. Review executable inputs (specification, dependency declarations,
frontend configuration and lockfiles) explicitly before changing the profile.
Keep the profile, system paths/hashes and receipts outside both source trees.
The frozen executor must match every helper blob in its accepted controller
commit; it must not launch from editable working files.

```powershell
python -I -S -B FROZEN_SCRIPTS/candidate_builder.py --config PRIVATE_PROFILE status
python -I -S -B FROZEN_SCRIPTS/candidate_builder.py --config PRIVATE_PROFILE discover
python -I -S -B FROZEN_SCRIPTS/candidate_builder.py --config PRIVATE_PROFILE run-once
python -I -S -B FROZEN_SCRIPTS/candidate_builder.py --config PRIVATE_PROFILE cancel --job BUILD_KEY
python -I -S -B FROZEN_SCRIPTS/candidate_builder.py --config PRIVATE_PROFILE retry --job BUILD_KEY
python -I -S -B FROZEN_SCRIPTS/candidate_builder.py --config PRIVATE_PROFILE recover --job BUILD_KEY
```

`build_materialize.py` copies and rehashes approved physical dependency bytes
into a new private attempt. It excludes cached bytecode, startup customizations
and weights, requires reviewed `.pth` bytes and reconstructs only verified
internal frontend aliases. `build_bootstrap.py` requires `-I -S`; it never calls
`site.addsitedir` or processes `.pth` lines. This is input integrity, not a
hostile-code sandbox or signed dependency provenance.

The fixed phases build Vite (120 s), package PyInstaller (300 s), verify (90 s)
and smoke the actual EXE (50 s), with a 180 s dependency-copy limit. Windows Job
Objects attach children atomically before execution; timeouts, cancellation or
controller exit terminate owned descendants. Resource gates require 2 GiB RAM
and GPU below 70 C to begin; they stop at 512 MiB headroom, 82 C or driver/hardware
incidents. They never close a user's application or invoke native models.

Full COLLECT origins must belong to this attempt or exact reviewed system DLL
inputs. The complete onedir rejects stale extras; all application modules must
match the accepted Git source. Acceptance requires all eleven current real
Qt/React/bridge/SQLite/shutdown checks, zero model requests and zero remaining
owned processes. Empty or partial receipts never pass. This is **non-native**
validation; no inference, performance or redistribution claim follows from it.

The journal records both candidate pointers before promotion. Only a complete
verified replacement advances the atomic private pointer. Previous directories
remain available; no candidate or production files are overwritten. Interrupted
phases require explicit recovery/retry; there are at most two attempts per job.
Interrupted promotion rechecks full payload and receipt identities before
accepting it. Unknown pointer changes require reconciliation, never guessing
success from file existence. Discovery retries persist with backoff and stop
after eight failures without blocking other queued jobs or source publication.
Preflight resource deferrals wait five minutes without consuming a build
attempt or closing user apps. Preflight network retries have an eight-failure
attention boundary; actual interrupted/failed build attempts remain explicit.

Permanent Windows task deployment and actual executor acceptance must be
recorded separately after accepted exact-source CI. Tests using disposable Git
and synthetic builds are queue-contract evidence, not evidence of a real build.

`candidate_runner.py` is the fixed scheduled entry point. Its private manifest
binds the accepted commit directory, all ten helper hashes, private profile and
both console/windowless interpreter hashes. Launch it with `-I -S -B`; never
point a task at the editable repository. Use a separate limited-user task with
IgnoreNew concurrency and a bounded execution limit. Keep its definition,
previous manifest and installation journal before changes. A frozen-worker or
profile update requires a new exact accepted CI identity and explicit journaled
verification; do not overwrite accepted frozen files. Source publication keeps
its existing independent task and runtime. The overall build budget is fifteen
minutes, in addition to the smaller individual phase limits.

No candidate installation is automatic. Native provenance/licensing and clean
Windows acceptance still block downloadable binary publication. Model weights,
private configuration, user databases and raw test profiles must never enter
source commits or release artifacts.
