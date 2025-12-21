# Copilot / AI Agent Instructions for this repository

Purpose: quickly orient an AI coding agent to be productive in this Flask-based AI Gateway.

- **Big picture:** This is a small Flask service (`app.py`) that exposes a REST API to upload files and query them via the local `gemini` CLI. Uploaded files live under the user's home `~/.gemini_uploads/` (constant `UPLOAD_DIR` in `app.py`). The service supports PDF extraction (via PyPDF2 when installed), a context-mode for session-persisted files, and an interactive auth flow that drives the `gemini` CLI through a pseudo-terminal.

- **Key files:**
  - `app.py` — main Flask app and all routes (auth, upload, generate). See [app.py](app.py).
  - `README.md` — user-facing API examples and notes. See [README.md](README.md).
  - `IMPLEMENTATION_GUIDE.md` — migration/refactor guidance and important utilities (validators, errors, process manager). See [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md).
  - `config.py`, `utils/` — configuration and helper utilities referenced by the refactor plan.

- **Architecture & integration points (how things connect):**
  - Client -> Flask endpoints (`/api/upload`, `/api/generate`, `/api/auth/*`).
  - `app.py` runs `gemini` CLI via `subprocess` (non-streaming uses `subprocess.run(['gemini', 'chat', prompt], ...)`) or via unbuffered REPL (`Popen` with stdin/stdout) for streaming.
  - Authentication uses a PTY-based flow (`GeminiAuthenticator` in `app.py`) that starts a `gemini` process and scrapes an auth URL; the endpoint `/api/auth/submit` writes codes into the PTY.
  - File access: generate endpoint runs with `cwd=UPLOAD_DIR` so `gemini` can read uploaded files. The app enforces a strict read-only `SYSTEM CONTEXT` in prompts — agents must not modify files or suggest writes.

- **Project-specific conventions & patterns** (examples you must follow):
  - File naming: uploads are saved as `<name>_<md5hash><ext>` (see `upload_file` in `app.py`) — use this pattern when referencing files.
  - Context mode: clients set `context_mode=true` and supply `X-Session-ID` to persist files across requests; otherwise the server clears non-context files between sessions.
  - Streaming: when `stream: true` the app yields `data: ...` lines (text-stream) from `generate()`; expect small-chunk reads and heartbeat messages.
  - PDF handling: only performed if `PyPDF2` is importable; extracted TXT is named `<name>_<hash>.txt` and included in `extracted_txt` response.
  - Strict system prompt: `app.py` prepends a `SYSTEM CONTEXT` block forbidding writes/commands — agents should obey it literally.

- **Developer workflows & commands** (what works now):
  - Install deps: `pip install -r requirements.txt` (or `requirements_improved.txt` if following refactor notes).
  - Run locally: `python app.py` (server listens on `0.0.0.0:5000`).
  - Upload example (from README): `curl -X POST http://localhost:5000/api/upload -F "file=@mydoc.pdf"`
  - Generate example (non-stream): see `README.md` example using `POST /api/generate` with JSON `{"prompt":"...","files":["..."]}`.

- **What to look for when modifying code** (quick checklist):
  - Keep `UPLOAD_DIR` usage and `cwd=UPLOAD_DIR` in subprocess calls so `gemini` can access files.
  - Preserve the PTY-based auth flow if you need programmatic auth; the logic is in `GeminiAuthenticator` in `app.py`.
  - Keep the `SYSTEM CONTEXT` text generation logic that lists available files — changes here affect AI safety constraints.
  - Avoid changing stdout debug prints indiscriminately; they are relied on by operators for long-running tasks and auth monitoring.

- **Testing & debugging tips:**
  - Use small files and `stream: true` to reproduce streaming behavior quickly.
  - If `gemini` hangs, check server logs (stdout) — `app.py` prints process heartbeats and PTY debug lines.
  - Auth status is detected by files under `~/.gemini/` (e.g., `oauth_creds.json`, `settings.json`). The authenticator probes those files — read `GeminiAuthenticator.check_auth_status` for exact rules.

- **Do not invent policies** — only follow behaviors visible in code and docs above. If a behavior is not present in these files, ask the maintainer before adding it.

If any section above is unclear or you'd like me to expand examples (route-level tests, or a small contributor README), tell me which parts to expand. 
