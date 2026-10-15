# Source-export verification — 16 October 2026

## Preserved baseline

The latest verified engineering checkpoint was read before export. All 87 recorded source/test file hashes matched on entry. Installed and isolated-candidate executable hashes matched their checkpoint identities. This phase changes no application features, model assets or production data and installs nothing.

All 18 root application Python modules are exported byte-for-byte. The frontend source, public assets, dependency lockfile and current PyInstaller spec are retained. The staging README/configuration/build guidance is curated rather than copying private engineering reports.

## Export-only adjustments

- Core requirements include actual Pillow/NumPy/psutil imports omitted from the old list; optional voice/build requirements and observed Windows constraints are provided.
- `AGENTS.md` now directs isolated builds instead of overwriting an installed package.
- `test_handoff_acceptance.py` is excluded because it depends on private image/PDF assets. Three private-PDF cases in `test_sprint.py` and two private-image cases in `test_fast_recolor_routing.py` are excluded from the exported copies. Existing synthetic coverage is retained.
- The AudioWorklet test finds Node.js through PATH rather than a developer-specific absolute path.
- A sensitive-data test now assembles its clearly synthetic token shape at runtime, avoiding a credential-shaped literal in the repository; its detection assertion is retained.
- The retained Qt probe uses a synthetic image instead of a private generated image and no longer reuses a prior private inference report. Optional documentation capture uses synthetic content. Other packaged/live-model harnesses are excluded because their environment/asset assumptions have not been made portable and safe.
- The old release batch script and older alternative spec are excluded. Their private sibling-folder assumptions and DLL deletion must not become a fresh-checkout release path.

These adjustments affect only the staging copy; original tests, build scripts and application files are unchanged.

## Actual checks

Checks ran in a **separate verification copy** using the existing Windows Python 3.12 environment. No production application/model inference was started.

| Check | Result |
|---|---|
| Python synthetic unit/integration suite | 578 tests run in 22.982 s: **577 passed, 1 expected skip**, zero failures/errors |
| Expected skip | Private historical model evidence is deliberately not shipped |
| Fixed offline evaluation dataset | **28/28** component checks passed; no live model quality score |
| Frozen-lockfile frontend install | Passed using pnpm 11.25.0, in a new dependency store outside the original project |
| Frontend production compilation | Passed, Vite 6.4.3; build reported 2.95 s |
| Qt WebEngine frontend probe | **75/75** synthetic assertions; zero real requests |
| Documentation screenshots | Actual Qt WebEngine captures with synthetic content; dark/light images visually reviewed |
| Existing Python environment dependency check | `pip check`: no broken requirements |
| Sanitized sensitive-data test follow-up | 113 tests passed in 0.487 s; token detection behavior retained |
| Export scanner regression fixtures | 5 checks passed: clean input, private/model assets, token, personal path and invalid Python detection |
| Git ignore contract | 17 representative private/large/generated paths excluded; no application module excluded |

The exported tests differ from the private checkpoint's 586-case suite by eight private-asset-dependent cases. The skip is not counted as a pass. Genuine AI inference, new native packaging and clean-machine Python dependency installation were **not** performed in this export audit. Do not interpret these checks as full production acceptance.

## Safety and completeness

The source package is allowlisted. User databases, models, voice assets, private files, raw prompts, traces, caches, installed/candidate binaries and protected backups are absent. `scripts/audit_repository.py` provides a repeatable bounded scan for forbidden assets, common credential patterns, personal Windows paths, large files and Python syntax. It reports locations without secret values. Manual source/metadata/screenshot review complements the heuristic; no scanner can prove complete confidentiality or ownership.

The full local preparation report, inventory, test logs and copy provenance are **outside this repository** and must not be added. No Git remote, upload or public repository was created. Review the source package and licensing decisions before publishing privately.

The final source export contains no files at GitHub's 50 MiB warning threshold. Its largest file is `app.py` at 400,970 bytes. All 18 application modules and genuine frontend files match the original source. The original workspace's 75,949 inventoried file sizes/modification times and all 137 protected checkpoint file stats match after verification; original source/test/build hashes and the installed EXE hash also match. Private database/config fingerprints are retained only in the separate local audit folder.
