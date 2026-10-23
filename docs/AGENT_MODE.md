# Agent Mode — implementation and acceptance status

This extension belongs to the existing Python/PySide6/React application. It is off by default. Ordinary chat, document routing, image generation and voice keep their existing controllers. No model weights or model loaders were replaced.

## Implemented

- `DesktopAgentController` and a bounded Qt worker connect the existing chat interface to `AgentRuntime`.
- `LocalAgentModel` uses the existing `EngineManager`, resource lease, tokenizer, selected local backend and cancellation connection. It does not launch another LLM runtime.
- The current planner is a strictly validated **JSON fallback**. This is not native function-calling or grammar-constrained output acceptance. Invalid JSON, duplicate keys, unknown operations and unexpected arguments dispatch nothing.
- The existing task-ID parser/completeness validator prevents a final response missing numbered sections from becoming a fully completed task. This structural check does not prove semantic correctness.
- Official MCP Python SDK **2.3.0** is explicitly pinned. The owned server uses `MCPServer`; the client uses `Client(StdioServerParameters(...))`. Only stdio is enabled. SDK transport/API references: [official client documentation](https://py.sdk.modelcontextprotocol.io/client/), [official server documentation](https://py.sdk.modelcontextprotocol.io/servers/).
- If the optional SDK is installed, its imports and public AnyIO backend initialization run once before PySide installs its feature-import hook. This repairs a reproduced >35-second first-tool import stall in the Qt worker. Measured SDK bootstrap time is exposed as `sdkStartupMs`; a dependency failure disables Agent Mode with a visible reason while ordinary chat remains available. No model or MCP server is started during this bootstrap.
- One reviewed MCP dispatch capability (`workspace_operation`) is discovered and checked. The host owns the operation catalog; server descriptions cannot inject new model tools. Arbitrary third-party servers, shell commands, URLs, sampling and remote transports are unavailable.
- Workspace tools: bounded listing/literal search, text reads, file hashes, static Python import/function inspection, copy-only patch preparation, approved new text files/edited copies/directories, existing PDF/DOCX extraction adapters, CSV inspection and read-only SQLite schema/queries. The existing system-information tool is reused.
- Writes require a one-use approval tied to immutable validated arguments. A per-connection authenticated grant is verified again inside the server. Approval secrets never enter model context or task history. Existing files cannot be overwritten, even after approval.
- Windows file operations use retained directory handles and relative `NtCreateFile` opens. Traversal, alternate streams, reparse points/junctions, hard links, reserved/ambiguous names and known credential/application paths are rejected. Reads and writes are bounded and verified. File listing also bounds directory traversal.
- SQLite operates on a bounded in-memory snapshot, with extension loading unavailable, query-only/trusted-schema settings, an authorizer, VM-step limit and result limits. Conversation schemas are rejected. The source database is not modified.
- Eight planner steps by default, a 120-second task budget (maximum 180), per-call/result limits and a conservative total token budget. Actual prompt tokens are counted by the existing local runtime before generation. Tool observations are untrusted data. Oversized observations are explicitly omitted with a digest; no fabricated summary is substituted.
- Task state/events use additive tables in the existing workspace database after an online backup and atomic schema transaction. Hidden reasoning and full tool bodies are not persisted. Passive history reads do not migrate the database. Interrupted tasks are never automatically replayed; another window's unfinished journal is preserved.
- The existing chat composer has an Agent toggle, chosen-workspace panel, compact activity/history, approval dialog with exact content/diff, Allow once/Deny/Cancel, and truthful error/cancellation states. Rejected starts preserve the draft. Completion is recorded only after MCP teardown succeeds.

## Limits and fail-closed boundaries

This is a development-source extension, **not a released or fully accepted agent product**.

- Genuine GPT-driven tool selection, reasoning correctness, cancellation while native inference is active, context/latency and loaded-model reuse still require bounded native acceptance. Scripted planners in tests do not prove model intelligence.
- Native function calling and constrained generation are not advertised as verified capabilities. The JSON fallback fails closed on invalid output.
- Original-record historical FTS retrieval is exposed through a **host-only** adapter to the existing opt-in ConversationMemory index. Excluded/trash/deleted messages and the current command are omitted; the workspace MCP subprocess cannot query the private application DB. Citation IDs and URLs are checked using existing provenance guards. This does not establish semantic entailment or genuine model synthesis quality. Normal-chat memory is unchanged.
- Host-owned image/attachment inspection, bounded image search and approved new PDF/Word creation are integrated with the selected text planner. Source hashes and provider evidence are retained. Native model correctness remains separately gated. Microphone and speech playback remain desktop interfaces; arbitrary MCP authority is unavailable.
- Arbitrary Python, shell, deletion, protected database/config writes and network operations are unavailable. A subprocess timeout is not an execution sandbox. `run_approved_test`/restricted code execution must not be exposed until real Windows isolation and its acceptance exist.
- Patch application creates a separate new file. In-place modification is deliberately unavailable. Before approved dispatch, the existing SQLite store records the workspace directory identity, exact destination, argument digest, expected output hash/size and original-source hash where applicable. Only an acknowledged result plus independent file identity/content readback becomes VERIFIED. Interrupted operations retain DISPATCH_INTENT. The recovery UI performs bounded read-only inspection, distinguishes matching unconfirmed contents from partial/changed or unavailable artifacts, and never infers success from absence or replays a write. Choose a new destination for another approved attempt; originals and partial artifacts are retained. This journal does not make file creation and SQLite one atomic filesystem transaction.
- Document extraction requires the existing reviewed document dependencies. A missing parser produces a tool error; nothing is downloaded automatically. Parser security is not established solely by filename/size validation.
- Offline design uses stdio with no configured network transport or telemetry exporter. A truly disconnected/OS-isolated environment has **not** been accepted by these tests.
- These guards address tool/model path and approval attacks and ordinary concurrent edits. They are not an OS security boundary against an administrator or same-user attacker replacing trusted source/interpreters/dependencies.

## Development dependencies and packaging

Use a **new isolated development environment**, not an accepted/frozen build environment. Python 3.12 Windows is the measured environment. `requirements-agent.txt` declares the SDK; `requirements-agent-windows-py312.lock` pins the 29 downloaded dependency wheels and hashes used for development. Install reviewed wheels with `--require-hashes --only-binary=:all:`. The lock is platform-specific and is not legal/redistribution clearance.

Keep the existing reviewed Qt/application dependency setup. The SDK's Windows `pywin32` bootstrap must be initialized normally by the isolated interpreter. Merely injecting its site-packages directory into another interpreter was tested and failed (`pywintypes` missing); do not execute arbitrary `.pth` files to bypass this.

The installed EXE, frozen build worker/profile, source-publication mechanism and production data are not updated by adding these source files. Packaging must pass the established versioned environment/provenance and candidate-adoption workflow, including SDK metadata and native/transitive dependency closure. `Offline GPT.spec` has not been silently altered to bypass that workflow. An ordinary build without the optional reviewed SDK dependencies reports Agent Mode unavailable and keeps normal chat usable.

## Reproducible tests

From the source directory:

```powershell
python -B -m unittest discover -s tests -q
python -B tests/qt_frontend_check.py
python -B tests/qt_image_settings_check.py --report-dir C:\IsolatedEvidence\image-ui
# Use an isolated interpreter containing the reviewed SDK and Qt dependencies:
python -B tests/qt_agent_check.py --report-dir C:\IsolatedEvidence\agent-ui
```

Report folders must be new, isolated directories. `qt_agent_check.py` uses the actual source Qt application, bridge, SQLite and genuine stdio server, with a **scripted test planner and zero model inference**. Its optional `--qt-site` accepts an explicitly selected development Qt dependency directory; it is not a production dependency/provenance mechanism. Tests create synthetic files only.

Native acceptance must wait for the established preflight (at least 7.5 GiB available RAM, GPU below 70°C, driver checks), remain sequential/bounded, and stop at 82°C, 512 MiB available RAM or hardware/driver incidents. Candidate building and production installation retain their separate authorization gates. No public binary or model release is authorized here.
