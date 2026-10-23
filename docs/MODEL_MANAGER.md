# Local Model Manager — current implementation

Settings → Model Manager imports references to existing local GGUF files. No
weights are downloaded, copied or deleted. Find in folder performs bounded
filename discovery in an explicitly selected Windows directory; discovered
names are not compatible-model approvals. Import each file for bounded metadata
inspection. Linked/reparse paths retain existing restrictions.

The registry uses the existing SQLite owner/lock and a pre-migration backup.
Passive listing does not create a new table. Import/selection/estimation runs in
the existing cancellable workspace worker, not on the Qt GUI thread. Selection
requires the actual native confirmation dialog, rechecks the reviewed header and
file observation, updates existing default-model settings and unloads the old
engine through its existing owner. A failed reload restores previous settings.
Config writes now use fsync + same-directory atomic replacement. Cancellation
after a committed registry/settings effect is explicitly acknowledged; it does
not falsely report that a committed change was rolled back.

Load/unload use the same EngineTextRuntime facade. They are not separate model
loaders. UI progress reports actual engine messages. CPU/auto controls in Models
& chat now survive both save and readback. Reference removal preserves weights
and rejects the active/default reference; it is not permanent weight deletion.

Details separate architecture, quantization, header/file identity, declared
context and unverified native capabilities. Known specialist/unknown families,
missing templates and split sets cannot be activated through this initial
onboarding path. Selecting a recognized text candidate is explicitly experimental;
it is not evidence of native or Agent compatibility. Hardware estimates are
advisory, with unknown hybrid allocations shown honestly.

Quantization labels follow upstream LlamaFileType metadata constants:
https://github.com/ggml-org/llama.cpp/blob/master/gguf-py/gguf/constants.py
They are not inferred from filenames or a claim about this backend binary.

The panel requires the versioned getModelLibraryVersion bridge capability. An
older installed backend shows an explanation instead of fake working controls.
Do not install source frontend assets alone to claim new backend functionality.

Not implemented/accepted yet: model download/resume, complete split/companion
onboarding, permanent model-file deletion, per-conversation model overrides,
native benchmark database, verified capability receipts, calibrated adaptive
hardware profiles or native multi-family Agent acceptance. The existing default
manual selector remains available. No application installation is implied.

The Agent catalog additionally provides a bounded arithmetic AST tool. Function
commas use strict syntax; the existing prose calculator retains its thousands
separator behavior. This tool does not execute arbitrary Python, project tests,
imports, file operations or terminal commands.
