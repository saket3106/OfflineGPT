# Third-party and licensing review

This is an engineering inventory, **not redistribution clearance**. No project license has been selected on the owner's behalf. Private GitHub hosting does not eliminate license obligations when sharing software or model assets. Code/branding ownership and all dependency obligations should be resolved before distribution.

## Important decisions

| Component | Observed license / issue | Action before distributing |
|---|---|---|
| PyMuPDF / MuPDF | AGPL or Artifex commercial licensing | Decide the application's compatible licensing/commercial route; do not assume a permissive proprietary bundle |
| PySide6 / Qt | LGPLv3/GPLv3/commercial options; modules differ | Review chosen modules, notices, replacement/relinking rights and corresponding source obligations |
| Qt WebEngine / Chromium | Multiple third-party licenses | Preserve the actual distributed Qt/Chromium third-party notices and applicable source obligations |
| phonemizer | GPLv3-or-later (observed package metadata) | Review how it is distributed/combined; separate subprocesses alone are not automatic clearance |
| eSpeak NG / loader payload | eSpeak NG GPLv3-or-later; loader wrapper rights distinct | Inspect the payload and preserve its license/source requirements |
| PyInstaller | GPL with application distribution exception | Review the exception and bundled components; do not label the whole application GPL just from the packager |
| GPT-OSS 20B, Qwen3-VL, Kokoro-82M | Official model cards list Apache-2.0 | Retain required notices; verify exact conversion/projector/voice assets separately |
| FLUX.2 Klein **Base 9B** | FLUX non-commercial license | Review gated model agreement, use/distribution limits and required notices; this is not the Apache 4B variant |
| FLUX VAE / converted encoder / upscaler | Exact artifact provenance not established here | Do not redistribute until exact source/terms are documented |
| Native llama/Whisper/FLUX bundle, CUDA and MSVC DLLs | Upstream engines are generally permissive, but exact private binaries are unidentified | Establish provenance and applicable vendor redistribution terms; no binaries included here |
| OfflineGPT brand graphics | Project-local icons; ownership provenance not independently established | Owner to confirm rights; no third-party desktop screenshots or proprietary fonts were copied |

Primary references: [PyMuPDF licensing](https://pymupdf.readthedocs.io/en/latest/faq/index.html), [Qt licensing](https://doc.qt.io/qt-6/licensing.html), [Qt WebEngine licensing](https://doc.qt.io/qtforpython-6.11/overviews/qtwebengine-licensing.html), [phonemizer](https://github.com/bootphon/phonemizer), [eSpeak NG](https://github.com/espeak-ng/espeak-ng), [PyInstaller license](https://pyinstaller.org/en/stable/license.html), [FLUX model card](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-9B).

## Python and frontend packages

`docs/dependencies-observed.json` records versions and declared licenses from the existing Python environment and direct frontend packages, where metadata is available. It is not a complete verified transitive SBOM or a replacement for license texts. The pnpm lockfile resolves frontend transitive dependencies; build a release SBOM and collect the exact installed license files for every shipped version before publishing installers.

Core Python dependencies include PySide6, PyMuPDF, python-docx, python-pptx, Pillow, NumPy and psutil. Optional speech packages include kokoro-onnx, ONNX Runtime, phonemizer and espeakng-loader. Their observed versions are pinned through the environment snapshot constraints; they have not been reinstalled from scratch in this phase.

Frontend code uses React/React DOM, Base UI, Motion, Lucide, Tailwind CSS, react-markdown, remark-gfm, rehype-highlight, clsx and tailwind-merge, with Vite/build plugins. Package metadata declares mostly MIT terms; **Lucide declares ISC**. Preserve notices from these and their transitive dependencies. The interface uses system font fallbacks; Windows font files are not included. The application does not vendor shadcn's upstream component repository as a separate dependency.

No third-party model/engine license has been replaced with a project license. A future `LICENSE` should follow an explicit owner decision and compatibility review, not a GitHub Desktop default selection.
