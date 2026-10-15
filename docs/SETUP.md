# Setup and isolated builds

These instructions are for a **separate source checkout**, never the installed application folder. No installation/update script is provided. Package downloads need network access; configured inference itself is local.

## 1. Prepare Python

Install Windows x64 Python 3.12. Open PowerShell in the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-voice.txt -r requirements-build.txt -c constraints-windows.txt
.\.venv\Scripts\python.exe -m pip check
```

The core requirements now include Pillow, NumPy and psutil, which the old requirements file omitted. Voice dependencies are separate because of their size/licensing. The packaged spec collects the voice packages; install them for that build. `constraints-windows.txt` records the observed build environment, not a cryptographically locked or freshly reinstalled environment. Review package origins and availability before treating it as a reproducible release lock. No existing environment is copied into the repository.

## 2. Build the frontend

Install Node.js and pnpm, then:

```powershell
Set-Location frontend-source
pnpm install --frozen-lockfile
pnpm build
Set-Location ..
```

Vite writes `../ui` relative to `frontend-source` and empties that **checkout-local generated folder**. Do not redirect it into the installed application. No manual copy from `frontend-source/dist` is needed. Dependencies and compiled `ui` output are ignored by Git.

## 3. Run safe tests

```powershell
.\.venv\Scripts\python.exe scripts\run_unit_tests.py
.\.venv\Scripts\python.exe evals\runner.py --offline-only
.\.venv\Scripts\python.exe tests\qt_frontend_check.py
```

The unit launcher creates an isolated temporary data/config profile and does not launch models. The Qt probe uses synthetic data and a mock bridge; run it with **no `--real` flag**. It checks actual UI rendering/interaction inside Qt WebEngine. Native inference tests, production acceptance captures and private fixtures are intentionally absent. The evaluation runner's offline flag is required to avoid live model evaluation; it scores a fixed synthetic dataset and does not certify general intelligence.

`docs/EXPORT_VERIFICATION.md` records what was actually run during export. A new machine must rerun these tests; passing in the developer's existing environment does not prove a clean reinstall.

## 4. Run the desktop shell with new data

```powershell
$env:OFFLINE_GPT_DATA_DIR = Join-Path $env:LOCALAPPDATA 'OfflineGPT-Development'
New-Item -ItemType Directory -Force -Path $env:OFFLINE_GPT_DATA_DIR | Out-Null
Copy-Item offline_gpt_config.example.json (Join-Path $env:OFFLINE_GPT_DATA_DIR 'offline_gpt_config.json')
.\.venv\Scripts\python.exe app.py
```

Choose a **new, empty development profile** the first time; do not overwrite a profile that already contains settings. The copy command above intentionally has no `-Force`. Some ancillary log/image/thumbnail paths still use the checkout, so keep it writable. Set model paths through Settings or the development configuration. No model files are included. Missing model/runtime errors are expected until the assets below are provided. Do not test against private production conversations.

## 5. Provision native engines and weights separately

See [MODEL_ASSETS.md](MODEL_ASSETS.md). Native engine binaries are **required external artifacts**, not ordinary Python dependencies. Do not download random DLLs to resolve an import error or mix DLLs from different engine builds.

- Chat/vision: compatible Windows x64 llama server **and its complete matching DLL set**. The controller checks names including `llama-server-impl.dll`, `mtmd.dll`, `llama-common.dll` and `ggml_llamacpp.dll`, and sends flags/protocol options including automatic fit/load mode. These requirements mean a generic upstream release may not be a drop-in substitute. Exact provenance/build settings of the existing private native bundle remain a release blocker.
- Images: compatible `stable-diffusion/sd-cli.exe` with its matching CUDA/native dependencies.
- Transcription: compatible `whisper-server.exe` and matching dependencies.
- Read Aloud: Python ONNX/phonemizer packages plus explicitly obtained Kokoro ONNX model/voice assets in the expected local directory.

No runtime installer or weight downloader runs automatically. Existing private binaries were excluded rather than silently relicensed. Record approved artifact URLs, upstream revisions, build flags, hashes and licenses before a redistributable build is considered reproducible.

## 6. Build the Qt application shell

After building `ui` and installing the Python build/voice packages:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist --workpath build "Offline GPT.spec"
```

Expected shell output: `dist\Offline GPT\Offline GPT.exe`. The retained spec is the one used by the current engineering checkpoint; it includes the UI, system prompt and collected Python voice resources, and filters a known Poppler/Qt ICU collision.

**This command alone does not produce the complete installed AI package.** It does not copy the separately required llama/Whisper/FLUX engines or weights. Stage reviewed, compatible engines/licenses and model references into a separate distribution directory only after resolving their provenance. A shell build without those assets may open its UI but cannot pass AI acceptance.

The older `build_offline_gpt.bat` and second spec were excluded: they assume private sibling folders, use older bundling rules and perform DLL deletion. No source-export build modifies the production app. A full native-enabled EXE was deliberately not rebuilt during this storage/privacy audit.

## Verification before any release

Require clean-checkout dependency installation/build, license notices, native artifact hashes, synthetic UI/data-integrity tests and bounded real inference acceptance for each enabled model. Include cancellation, warm/cold switching, memory pressure and thermal monitoring. Never install based only on mocks or a visible button. Keep runtime/model test records outside Git until sanitized.
