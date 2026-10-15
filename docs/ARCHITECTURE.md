# Architecture and privacy boundaries

## Source map

| Source | Responsibility |
|---|---|
| `app.py` | Qt desktop host, WebChannel bridge, persistence, chat/document/image/STT workers and orchestration |
| `integrated_runtime.py` | Configuration, app-owned local native server lifecycle, prompt fitting, streaming, compatibility backend and memory preflight |
| `intelligence.py`, `grounding.py`, `router.py`, `validators.py` | Request analysis, claim/task scope, dispatch and response checks |
| `tool_registry.py`, `tool_workflow.py` | Restricted tools, execution telemetry and bounded orchestration |
| `math_engine.py`, `logic_engine.py`, `code_engine.py` | Deterministic calculations, limited logic analysis and static code validation |
| `document_tools.py`, `rag_engine.py` | Reading, supported non-destructive edits, generation, exact search and local retrieval |
| `conversation_memory.py` | Historical source retrieval, provenance and coverage checks |
| `workspace_services.py` | Workspace indexing, Library/version state, recovery journals, diagnostics and image tools |
| `image_pipeline.py` | Prompt constraints, image validation, routing and profiling helpers |
| `voice_runtime.py` | Separate Kokoro ONNX synthesis worker and playback control |
| `memory_manager.py` | Context/token budgeting |
| `frontend-source/` | React UI source, curated brand assets, build configuration and dependency lockfile |
| `tests/`, `evals/` | Synthetic deterministic regressions, Qt UI probe and local evaluation runner |

## Runtime and storage

Qt loads the locally built `ui/index.html` and communicates through `DesktopBridge`. Chat/vision call app-owned local inference workers; FLUX is a direct CLI subprocess. Audio transcription and Read Aloud are separate systems. Sequential handoff/memory checks reduce overlapping model pressure but do not guarantee every workload will fit.

The default app stores state near its executable/source. `OFFLINE_GPT_DATA_DIR` changes the primary data/config/database profile; some logs, thumbnails and image/runtime paths still use `APP_DIR`. Run experimental builds in their own writable checkout. Do not launch them from or beside a production data folder. Relative model paths resolve against the application folder; Settings accepts explicit local paths.

Chats, history evidence, documents, voice captures, output images, local configuration, SQLite/WAL files and raw traces are private. None are shipped in this source export. Inference requests stay local in bundled mode; setup downloads, package managers, optional external links and a later GitHub upload require network access. A private GitHub repository is hosted by GitHub; do not upload personal data merely because its visibility is private.

## Trust and execution

Retrieved document/history content is untrusted. Source IDs and runtime evidence are distinct from the model's assertions. The controller accounts for required subtasks and derives method/completion fields from actual records. Syntax/structural checks cover specific contracts; they cannot certify unrestricted program semantics or all factual claims.

No unrestricted execution environment is implemented. Never run generated scripts or SQL merely to evaluate an answer. Raw fitted-prompt logging is opt-in and can expose private text; keep it off for normal use. Journals preserve ambiguity for interrupted Library operations; physical file absence is insufficient evidence of successful deletion.
