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

## Remaining automatic-build work

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

These verified components are foundations, not a completed automatic builder.
The remaining controller must capture and validate the approved Python/Node
environment and cached dependency identities, export immutable accepted inputs,
build the frontend and complete onedir in a new private directory, compare all
compiled modules and COLLECT sources, run the packaged smoke, and atomically
advance a known-good candidate pointer with rollback/recovery journals. It must
consume only completed source batches and avoid rebuilding unchanged inputs.

No candidate installation is automatic. Native provenance/licensing and clean
Windows acceptance still block downloadable binary publication. Model weights,
private configuration, user databases and raw test profiles must never enter
source commits or release artifacts.
