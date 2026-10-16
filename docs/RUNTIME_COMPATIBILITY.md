# Runtime compatibility boundary

The existing bundled GPT-OSS and optional LM Studio integrations now dispatch
through `ExistingTextAdapter`. EngineManager still owns resource leases, model
loading, cancellation, warm sessions, unloading and error handling. Existing
message contents, sampling parameters, callbacks and tool results pass through
unchanged. No model or backend is automatically selected or downloaded.

`runtime_contracts.py` defines a separate, conservative contract for future
bring-your-own-model support. Exact model, native engine, template and required
asset hashes, architecture, quantization, license identifier and context limits
are recorded. Hardware identity must include relevant backend/driver details.
Native acceptance is scoped to that identity, capabilities and tested context.
Changed identities, missing observations and unsupported capabilities fail
closed. Smaller models can have different independently validated budgets; the
existing GPT native-probe minimum remains 7.5 GiB available RAM and a measured
GPU temperature below 70 C. These are preflight conditions, not substitutes for
the existing driver checks, 240-second deadline and runtime stop monitoring.

The registry starts empty. A downloaded catalog, filename, parameter count,
model statement, deterministic test or shell smoke test cannot grant native
support. Its verifier is a trusted application-owned independent acceptance
service; a callback returning true records review and is not proof that the
review was honest. Synthetic tests exercise policy only and do not populate the
application with accepted native evidence. This boundary does not retroactively
revoke the existing GPT workflow or claim that existing optional tools have
passed new-model acceptance.

## Still required

- Authenticated/independently reviewed native receipt ingestion and registry
  persistence, including freshness and revocation.
- Safe local GGUF inspection/import, immutable model-file use and runtime
  provenance/architecture/template checks before loading a new model.
- A model manager, optional explicit download workflow, storage selection and
  model-specific onboarding connected to validated registry entries.
- A genuinely validated second model and runtime adapter. Ollama, AMD and
  integrated-GPU support have not been implemented or certified by this unit.
- Model-free native provisioning, redistribution licenses, clean-Windows
  packaged inference and installer/release acceptance.

No public model compatibility list or performance prediction is established by
these contracts. GPT-OSS native semantic acceptance remains separately gated.
