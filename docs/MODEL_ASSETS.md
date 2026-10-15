# External model and engine assets

No weights, voices, projectors, CUDA DLLs or native executables are committed. No automatic download is configured. Obtain assets only after reviewing their licenses and upstream provenance. Configure paths in an isolated development profile.

| Role | Expected configured asset | Required companion |
|---|---|---|
| GPT-OSS chat/reasoning | `gpt-oss-20b-MXFP4.gguf` | Compatible local llama server and complete matching DLL bundle |
| Vision/OCR | `Qwen3VL-8B-Instruct-Q4_K_M.gguf` | `mmproj-Qwen3VL-8B-Instruct-F16.gguf` and multimodal server support |
| Diffusion generation/editing | `flux-2-klein-base-9b-Q4_K_M.gguf` | Compatible `sd-cli.exe`, encoder and VAE |
| FLUX text encoder | `Qwen3-8B-Q4_K_S.gguf` | Correct architecture/quantization accepted by the image runtime |
| FLUX decoder | `flux2-vae.safetensors` | Compatible diffusion runtime/model family |
| Optional detail-aware upscale | `4x-UltraSharp.pth` | Explicitly verified weight provenance/license; not cleared here |
| Speech to text | `ggml-large-v3-turbo-q5_0.bin` | Compatible local `whisper-server.exe` and matching native dependencies |
| Read Aloud | `kokoro/model_quantized.onnx` | `kokoro/voices-v1.0.bin`, ONNX Runtime and speech/phonemizer resources |

These filenames describe configured expectations, not an endorsement of an arbitrary download with the same name. Quantizer identity, upstream revision, file SHA-256 and architecture must be verified separately. Inference and licensing are different from the MIT license of an engine wrapper.

## Upstream references

- [GPT-OSS 20B official model card](https://huggingface.co/openai/gpt-oss-20b): Apache-2.0; Harmony response formatting is required by the model. The local wrapper handles displayable answer extraction.
- [Qwen3-VL-8B-Instruct GGUF official model card](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF): Apache-2.0. A text-only language file without its matching projector is insufficient for images.
- [Qwen3-8B official model card](https://huggingface.co/Qwen/Qwen3-8B): check the exact converted encoder artifact and upstream terms.
- [FLUX.2 Klein Base 9B official model card](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-9B): **undistilled Base variant**, FLUX non-commercial terms. Do not confuse it with the distilled model or the Apache-licensed 4B family. This export changes neither weights nor sampling presets.
- [Whisper official project](https://github.com/openai/whisper) and [whisper.cpp](https://github.com/ggml-org/whisper.cpp): the current quantized model/server artifacts require exact revision and conversion provenance.
- [Kokoro-82M official model card](https://huggingface.co/hexgrad/Kokoro-82M) and [kokoro-onnx wrapper](https://github.com/thewh1teagle/kokoro-onnx): model card Apache-2.0; verify ONNX conversion and voice-asset notices separately.
- [stable-diffusion.cpp FLUX.2 guide](https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/flux2.md), [llama.cpp](https://github.com/ggml-org/llama.cpp): engine build documentation. A stock engine is not yet verified as compatible with this controller's entire protocol/flag contract.

## Fresh-checkout blockers

1. Native artifacts have no established exact upstream revision/build recipe in this export; the existing executable metadata does not identify vendor/version. Do not assume proprietary redistribution rights or replace the bundle blindly.
2. The retained PyInstaller spec packages Python/UI resources, not all native workers/weights. A release provisioning step is still required.
3. The upscaler's provenance and the precise VAE/voice-conversion terms need review.
4. No clean-machine native/inference acceptance was performed during the GitHub-preparation phase.

Maintain a future external artifact manifest containing role, upstream repository/revision, download URL, SHA-256, license/notice path, architecture, CUDA requirements and supported flags. Keep personal local model paths and download credentials out of Git.
