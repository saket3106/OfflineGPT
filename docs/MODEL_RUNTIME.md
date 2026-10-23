# Model-neutral runtime: implemented scope and acceptance

OfflineGPT retains EngineManager and the existing llama.cpp process, tokenizer,
prompt fitting, streaming, cancellation, resource lease and unload mechanisms.
`EngineTextRuntime` is a typed lifecycle facade over that same manager, not
another loader. The existing GPT-OSS and optional local LM Studio adapters remain.

## Model identity and behavior

`ModelInfo` captures bounded inspected GGUF metadata and immutable metadata
snapshots. Architecture comes from `general.architecture`, not the filename.
File observations/header hashes are not full tensor integrity or model license
approval. Stored tensor elements and a declared parameter count are labelled
separately; neither establishes active parameter count, memory residency or IQ.

`ModelBehavior` retains Harmony and reasoning-effort options for the existing
GPT-OSS adapter. Successfully inspected Qwen/Llama/Gemma/other models use their
server-side embedded templates, without GPT-OSS effort/budget fields. Reasoning
fields are not streamed to the UI; explicit think spans are additionally
filtered. Harmony token examples in non-Harmony content are not interpreted as
role channels. Existing metadata-inspection failures retain a labelled legacy
adapter fallback; this is not approval to import a new architecture or bypass
the existing hard-link/reparse inspector restrictions.

Declared context can reduce a launch configuration. Actual loaded context bounds
the Agent token budget. CPU selection reaches `-ngl 0`; auto remains the default.
GPU-setting changes and changed weight-file observations invalidate warm reuse.
Per-response identity snapshots are stored only for turns with actual model calls;
later model selection does not rewrite an earlier response's attribution. Existing
historical rows without an identity retain legacy normalization behavior.

## Capabilities and resource estimates

Reports separate declared metadata, backend support and native execution
acceptance. No new native evidence is registered. A recognized architecture,
valid template or successful HTTP transport test cannot enable verified tool,
vision, JSON, CPU/GPU or agent compatibility. Native receipt authorization stays
with the existing CompatibilityRegistry and independent verifier.

`estimate_memory` exposes separate weight-file, dense FP16 KV estimate, runtime
allowance and system-reserve quantities. It is advisory and never authorizes a
load. GPT-OSS/MoE/recurrent/hybrid allocation remains unmodelled without measured
evidence; in particular a dense KV formula is not used for Qwen3.5 or GPT-OSS.
The 7.5-GiB GPT native-probe minimum and thermal/critical-memory guards are unchanged.
CPU command selection does not prove the existing CUDA-linked payload runs on
a clean GPU-free machine.

## Explicitly unfinished

- Genuine Qwen3/Qwen3.5 compatibility, tool planning, model comparisons and
  performance acceptance; source fixtures are not native model evidence.
- Full model onboarding/catalog/download/removal/default/per-conversation UI.
- Verified automatic per-model hardware selection, calibrated allocator
  measurements and complete Eco/Balanced/Performance/Custom UI.
- Remaining legacy normalizers (including some routing, voice and export
  paths), external-runtime format negotiation and complete model switching
  acceptance across every document/multimodal path.
- Split-model/companion asset completeness, authenticated model receipts and
  safe native provisioning/distribution; do not download weights automatically.
- Packaged new backend acceptance. Production installation and the paused
  GitHub/candidate workflow require their existing separate authorization.

## Primary technical references

The upstream llama.cpp server documents model-metadata templates and server
options: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md

Upstream architecture names/metadata keys:
https://github.com/ggml-org/llama.cpp/blob/master/gguf-py/gguf/constants.py

Model-specific cards:
https://huggingface.co/Qwen/Qwen3-14B and
https://huggingface.co/Qwen/Qwen3.5-9B

These describe upstream behavior. They do not certify the installed binary,
particular quantization, companion files or this hardware configuration.
