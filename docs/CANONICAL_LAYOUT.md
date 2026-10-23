# Canonical repository layout and recovery

## Status

Version 3 canonical mode and the relocation primitive are source implementations.
They do not activate themselves, change task definitions, move an installation,
or certify an existing frozen worker. Legacy version 1/2 disjoint layouts remain
supported. Keep periodic tasks disabled unless the owner explicitly changes that
policy.

## Contract

A canonical configuration uses the same exact ordinary directory for `source`
and `repository`. Its `layoutContract` pins version 1, an integer adoption epoch,
root, Git configuration SHA-256, policy SHA-256, accepted ancestor commit, branch,
remote URL and an explicit absolute `productionRoot`. The external control record
must contain the matching epoch and canonical contract SHA-256. A missing or
changed binding fails closed; layout version alone is not adoption authority.

The installed production root must be recorded explicitly. Inferring it from a
new repository's parent could protect the wrong directory. Candidate profiles
must remain disjoint from the declared production root. Existing native inputs,
model files and user profiles retain separate acceptance and preservation gates.

## Existing pipeline, new layout gate

Canonical-mode publication acquires an OS byte lock at
`stateRoot/canonical-layout.lock`, outside the movable repository, then the
existing repository publication mutex. The new layout gate is part of the same
controller; it is not a service, timer, publisher or second queue. Runtime,
candidate-runtime and milestone upgrades use the manager's publication lock.
Candidate job operations retain their existing private queue locks, so normal
builds do not hold up source publication.

Windows rejects renaming a directory while its repository mutex handle is open.
The migration coordinator must therefore hold the external canonical layout
gate, candidate discovery lock and candidate builder lock in that order, verify
all supported controllers are quiescent, briefly acquire/release the existing
repository mutex, and keep the external gates held across the rename and final
validation. Never force a rename or drop concurrency protection to make it pass.
A stale canonical manager must fail without recreating the previous root.

## Relocation primitive

`canonical_relocation.Relocation` is a library for the reviewed versioned-adoption
coordinator, not a live command or acceptance shortcut. Its adapter must verify
exact accepted source/CI, registered worker/profile identities, disabled tasks,
quiescence and every reviewed external consumer before any transition. Passing
an arbitrary callback does not establish those facts. A production adapter and
live versioned acceptance must be separately reviewed before use.

Preparation records immutable plan bytes, original consumer bytes, complete
ordinary repository file identities, Git history/config/index/status identities
and review bindings in an external recovery journal. Symlinks, junctions, shared
Git object stores, redirected/executable Git configuration, linked files, case
collisions, overlapping destinations and cross-volume moves are rejected.

Apply journals rename intent, performs a same-volume rename, verifies contents
and Git identities, then atomically updates exact reviewed consumer bytes. Each
consumer may be only its recorded before or after state. Interrupted operations
require explicit recovery. File absence alone is never success: both/neither
location, unknown contents, changed index, altered backups or consumer drift
block the operation. Rollback verifies the same evidence and restores consumer
bytes and repository location without deleting unknown work.

The OS mutex file is recorded by file identity while held; it must remain an
untracked, single-link, one-byte coordination file. It is not an exclusion for
source or business data. All other repository file bytes are checked normally.

Simulated process interruptions and real Windows/Git tests do not certify
physical power-loss durability, clean-machine installation or native inference.
No rollback directory, model or user database may be retired merely because
these controller tests pass.

## Test fixtures

Controller fixtures may reuse a bounded, hash-sealed process-local image created
by the original disposable Git initializer. Every restoration creates ordinary
independent files, a separate bare remote and freshly rebound configuration.
There is no disk cache, hardlink sharing, pickle or cached test result. Mocked
clock/subprocess initialization is not reused. Test bodies, real Git operations,
privacy/integrity checks, all test IDs and the 600-second acceptance policy remain
unchanged. Corruption, unknown absolute references, unsafe links, incompatible
Git environments and oversized fixture images are rejected.

## Frozen source boundary

The hash-pinned frozen runner permits an exact same-root version 3 configuration
only after validating the complete layout contract and external epoch receipt.
Legacy overlap and nested layouts remain rejected. It compiles the verified
validator source bytes directly, so an adjacent timestamp `.pyc` cannot replace
that boundary. A bare version number never grants canonical-mode permission.
Source-safety CI explicitly runs these new no-model regression groups.
