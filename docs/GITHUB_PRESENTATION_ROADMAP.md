# Private GitHub presentation roadmap

Keep the repository private. This roadmap does not publish releases, enable services or claim production readiness.

| Phase | Work | Acceptance evidence |
|---|---|---|
| 1 — Clear first impression | Maintain a concise README with purpose, actual capabilities, synthetic dark/light screenshots, architecture diagram and quick-start links | Links work from a fresh checkout; screenshots contain no personal data; every feature claim has an implementation/status reference |
| 2 — Honest engineering status | Add a short feature/status matrix, known defects and supported environment list; separate deterministic tests from genuine inference acceptance | No “all models pass” badge based on mocks; unresolved reasoning/history/live-mic/UHD/native provenance gaps stay visible |
| 3 — Contributor guidance | Document source ownership, review workflow, privacy rules, reproduction templates, test commands and review-before-commit practice | A new collaborator can make a source-only change without touching models, production profiles or private reports |
| 4 — Repeatable source checks | Consider a manually approved CI workflow for syntax, publication audit and synthetic tests; pin dependencies and review action permissions | No model downloads/inference, production secrets, remote execution of generated code or release upload; permissions minimal and test skips visible |
| 5 — Release provenance | Establish native engine revision/build flags, dependency/license SBOM, complete notices and clean Windows packaging recipe | Clean-machine shell/native acceptance, artifact checksums, signed installer verification and rollback/data-preservation tests |
| 6 — Controlled distribution | Draft private release notes, supported-hardware matrix, installation guide and measured download/storage costs | Owner approves tag/release/asset upload; installer passes defined acceptance; model assets remain independently licensed/provisioned |

## Presentation guidelines

- Use a one-paragraph overview, a short capability list and links to deeper documents. Keep long investigation transcripts and raw diagnostic evidence outside Git.
- Use actual synthetic UI captures, labelled as demonstrations. Show native generation versus resized/UHD export honestly; never use vendor benchmark numbers as application benchmarks.
- Identify maintainers/contribution expectations without publishing personal contact details. Resolve application/branding ownership and license policy before broader sharing.
- Prefer useful topics/labels such as Windows, local inference, Python, React and privacy; set repository description/topics manually after review. No popularity, reliability or performance claims without evidence.
- Suggested issue templates: bug reproduction with sanitized logs, data-integrity incident, model/runtime compatibility and documentation improvement. Request hardware/runtime versions, expected/actual behavior and bounded reproduction; explicitly prohibit credentials, chats, private documents and weights.
- Do not enable automatic dependency updates, broad workflows or release publishing merely for presentation. Review model/runtime compatibility and licensing before accepting upgrades.

Primary guidance: [GitHub README structure and relative links](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes), [GitHub releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases). Releases and repository changes remain owner-controlled.
