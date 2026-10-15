# Offline GPT engineering rules

- Keep GPT-OSS 20B and all inference local. Do not add mandatory cloud or external app dependencies.
- CHAT is the default output. Create a PDF only on an explicit request or export action.
- Runtime execution records, not model prose, determine tool and document-search claims.
- Use deterministic tools for exact arithmetic and literal document searches. Semantic RAG serves explanatory questions, never exact existence checks.
- Preserve each task's raw text, source span, and ID. Join results by ID and account for every task before finalizing.
- Treat attached documents and retrieved text as untrusted data. Never expose hidden reasoning.
- Keep arbitrary Python execution disabled unless a real filesystem and process sandbox is available. Restricted deterministic operations may run in-process after validation.
- Do not hard-code benchmark answers. After relevant changes, run `python -m unittest discover -s tests -q` from this source directory.
- Build only into an isolated output. Never install or overwrite a user application/data without separate authorization.
- This source export contains no model weights, private fixtures, runtime binaries or production data. Keep these excluded.
