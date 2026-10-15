# Lightweight Windows distribution plan

## Practical first target

Use the existing **x64 onedir Qt/Python application** as the first packaging target, wrapped in a signed per-user conventional Windows installer after acceptance. Do not promise a tiny self-contained download: Qt WebEngine, Python and CUDA runtimes already occupy substantial space. The measured source repository is small because it excludes runtime/model assets; that does not make the installed AI application equally small.

Keep MSIX as a later option after validating process launches, writable paths and AppData behavior. Microsoft's [desktop packaging preparation guidance](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-prepare) describes compatibility constraints. Current `APP_DIR` log/image/thumbnail assumptions must be fixed through a reviewed migration before a read-only installation location is accepted.

## Separate payloads

| Payload | Proposed handling | Acceptance condition |
|---|---|---|
| Application/UI | Versioned app directory; signed installer; source-linked build manifest | Fresh Windows setup, Qt startup, bundled UI, uninstall and app rollback pass |
| Native engine packs | Separate immutable llama/vision, FLUX and Whisper packs with matching DLLs | Exact provenance, upstream revision/build flags, redistribution rights, supported protocol/CLI and GPU/driver compatibility verified |
| Models/voices/projectors | Owner-selected external local model store; reuse validated installed assets | Explicit consent for new acquisition; hashes/licenses checked; no silent replacement/quantization; model switching accepted |
| User data | Independent per-user data location, retained on uninstall by default | Transactional schema migration, consistent SQLite/WAL backup, Library/version/journal preservation and recovery tests |
| Diagnostics | Private opt-in export with redaction | No raw prompts, paths, credentials or personal content in public bug reports |

## Reduce size without risking working assets

1. Future app updates replace code/UI and only changed compatible engine packs, leaving model weights untouched.
2. After runtime-path validation, reference one approved Whisper weight copy and one shared model store. The earlier audit confirmed duplicate Whisper content; it did **not** authorize deletion.
3. Keep release debug/test caches and source-only fixtures out of packaged runtime directories. Preserve required Qt/Chromium resources and license notices.
4. Keep small app/engine rollbacks and separate user-data backups rather than copying every model on every update. Any existing protected rollback retention change requires separate approval.
5. Measure actual per-payload installed/download sizes after creating a clean candidate. Do not choose an arbitrary size target, remove DLLs blindly or compress/quantize the user's working assets.

## Current blockers

- Exact native bundle provenance/build reproducibility and compatibility are unresolved.
- PyMuPDF AGPL/commercial, Qt/Chromium, GPL speech dependencies and FLUX Base 9B non-commercial obligations require explicit decisions and notices. Model conversion/voice/upscaler provenance needs artifact-level review.
- Full clean-machine Python/native packaging and comprehensive genuine model acceptance have not been established by source synchronization.
- Current application-directory writes, true power-loss recovery, live-microphone accuracy and UHD thermal acceptance remain distinct engineering workstreams.

## Release gates

Record a reviewed source commit and full dependency/artifact manifest. Build once into an isolated candidate. Verify signatures, hashes and license notices; run synthetic data-integrity/UI tests, then authorized bounded genuine inference and switching/cancellation/thermal tests. Test installation/update/uninstall and rollback with isolated profiles. Promote only after owner approval; never overwrite production just because source tests passed.

Use approved private release artifacts rather than committing installers/engines into normal Git. GitHub releases are attached to tags and visible to repository readers; keep asset access and licensing under review. Check current upload limits before selecting artifact layout. See [GitHub releases documentation](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

No installer, model migration, cleanup, download, release or upload is performed by this plan. See [INSTALLER_PLAN.md](INSTALLER_PLAN.md) for the longer architecture proposal.
