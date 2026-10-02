# AI Gateway API

A RESTful API gateway built on Flask that interfaces Google's `agy` CLI engine. It handles file ingestion (text, PDFs, images), session-isolated execution environments, and streaming responses via Server-Sent Events (SSE).

---

## Features

* **Multimodal File Ingestion:** Supports text files, PDFs (automatic text extraction), and raw images (PNG, JPG, WebP, GIF) for visual reasoning and OCR.
* **Per-Session File Isolation:** Uploads are partitioned into isolated session sandboxes (`~/.gemini_uploads/<session_id>/`) preventing cross-user file collisions or overwrites.
* **Process Teardown & Streaming (SSE):** Streaming responses over `text/event-stream` with automatic process termination when clients disconnect or abort.
* **Non-Streaming Mode:** Direct JSON responses for synchronous backend integration.
* **CLI Engine:** Powered by Google's `agy` CLI in headless, read-only mode.

---

## Environment & Configuration

Configurable via environment variables (see `config.py`):

| Variable | Default | Description |
|---|---|---|
| `PORT` | `5055` | Server listening port |
| `HOST` | `0.0.0.0` | Bind address |
| `UPLOAD_DIR` | `~/.gemini_uploads` | Root directory for session storage |
| `MAX_FILE_SIZE` | `52428800` (50MB) | Maximum file size in bytes |
| `GEMINI_TIMEOUT` | `300` (5 min) | Subprocess timeout limit |

---

## API Reference

### 1. Upload File
`POST /api/upload`

Upload a file (text, PDF, or image) to the caller's session sandbox.

#### Headers
* `X-Session-ID` *(optional)*: Session identifier string. Defaults to `'default'`.

#### Request Body
**Option A: Multipart Form Data**
* `file` *(required)*: Binary file payload.
* `context_mode` *(optional, default `false`)*: Set `true` to persist file across multiple prompts; `false` clears prior temporary uploads for this session.

**Option B: JSON Payload**
```json
{
  "filename": "chart.png",
  "file": "<base64-encoded-string>",
  "context_mode": false
}
```

#### Response (`200 OK`)
```json
{
  "success": true,
  "filename": "chart_a1b2c3d4.png",
  "extracted_txt": null,
  "size": 45120,
  "path": "/home/user/.gemini_uploads/session_123/chart_a1b2c3d4.png",
  "context_mode": false,
  "session_id": "session_123"
}
```
*Note: For PDF files, `extracted_txt` contains the filename of the plain-text extraction.*

---

### 2. Generate Completion
`POST /api/generate`

Submit a prompt and optional file references to generate an answer.

#### Headers
* `Content-Type: application/json`
* `X-Session-ID` *(optional)*: Session identifier string.

#### Request Body
```json
{
  "prompt": "What does this image show?",
  "files": ["chart_a1b2c3d4.png"],
  "stream": false,
  "session_id": "session_123"
}
```

Or multi-turn conversation format:
```json
{
  "messages": [
    {"role": "user", "content": "What is in the file?"},
    {"role": "model", "content": "It contains quarterly revenue tables."},
    {"role": "user", "content": "What was the Q3 revenue?"}
  ],
  "files": ["revenue_a1b2c3d4.csv"],
  "stream": true,
  "session_id": "session_123"
}
```

#### Parameters
| Parameter | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | Either `prompt` or `messages` | Direct query text |
| `messages` | array | Either `prompt` or `messages` | Multi-turn chat history array |
| `files` | array | No | List of uploaded filenames (strings or `{filename: ...}` objects) |
| `stream` | boolean | No (default `false`) | `true` for SSE stream, `false` for standard JSON |
| `session_id` | string | No | Explicit session ID if header is omitted |

#### Responses

**Non-Streaming (`stream: false`):**
Returns `200 OK` with JSON:
```json
{
  "response": "The image shows a dark-mode web application with an active chat session..."
}
```

**Streaming (`stream: true`):**
Returns `200 OK` with `Content-Type: text/event-stream`:
```
data: [SERVER] Initializing AI...

data: The

data:  image shows

data:  a dark-mode interface...

data: [DONE]
```

*Note: If the client aborts or closes the connection mid-stream, the backend terminates the underlying `agy` subprocess immediately.*

---

### 3. Account Pool & Health
* `GET /api/accounts` — Returns pool statistics, active account, and cooldown status:
```json
{
  "total": 2,
  "active_account": "primary",
  "accounts": [
    {
      "id": "primary",
      "email": "primary@gmail.com",
      "status": "ready",
      "cooldown_until": null,
      "success_count": 42,
      "fail_count": 1,
      "last_used_at": "2026-10-02T01:00:00Z"
    }
  ]
}
```

### 4. Authentication
Endpoints used by the web interface when OAuth credentials need manual authorization:

* `GET /api/auth/status` — Returns `{"authenticated": true/false, "has_url": true/false}`.
* `GET /api/auth/url` — Returns `{"url": "https://accounts.google.com/..."}` if OAuth code flow is active.
* `POST /api/auth/submit` — Submit auth verification code: `{"code": "..."}`.
* `POST /api/auth/terminate` — Force terminate the interactive auth subshell.

---

## Multi-Account Quota Rotation System

To prevent service downtime when Google's quota or rate limits hit (`429`, `RESOURCE_EXHAUSTED`), the gateway features an automatic multi-account pool. When the active account exhausts its quota, it automatically enters a 60-minute cooldown and seamlessly fails over to the next available account.

### CLI Management (`manage_accounts.py`)

```bash
# List all accounts in the pool and their real-time cooldown status
python manage_accounts.py list

# Import your currently authenticated local agy session
python manage_accounts.py import-current primary

# Interactively authenticate and onboard a new Google account
python manage_accounts.py add backup_account

# Verify health and latency of all accounts in the pool
python manage_accounts.py test

# Remove an account from the pool
python manage_accounts.py remove backup_account

# Sync accounts and codebase directly to production and restart service
python manage_accounts.py sync-to-prod
```

---

## Supported File Types

| Category | Extensions | Processing Method |
|---|---|---|
| **Text Documents** | `.txt`, `.md`, `.csv`, `.json` | Read directly into context |
| **PDF Documents** | `.pdf` | Extracted to plain text via `PyPDF2` |
| **Images** | `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` | Multimodal visual inspection via `agy` |

---

## Quickstart

### Prerequisites
* Python 3.9+
* Google `agy` CLI installed (`which agy`) and authenticated (`~/.gemini/` credentials)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Start the Server
```bash
# Uses default port 5055 (or override via PORT=...)
python app.py
```

### 3. Verify Health
```bash
curl http://localhost:5055/api/auth/status
```

---

## Usage Examples

### Text Query (Non-Streaming)
```bash
curl -X POST http://localhost:5055/api/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Calculate 15 * 12", "stream": false}'
```

### Document Query (Streaming)
```bash
# 1. Upload document
UPLOAD=$(curl -s -X POST http://localhost:5055/api/upload \
  -H "X-Session-ID: my_session" \
  -F "file=@notes.txt")
FILENAME=$(echo $UPLOAD | jq -r '.filename')

# 2. Query document
curl -N -X POST http://localhost:5055/api/generate \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: my_session" \
  -d "{\"prompt\": \"Summarize these notes\", \"files\": [\"$FILENAME\"], \"stream\": true}"
```

### Image Analysis
```bash
# 1. Upload image
UPLOAD=$(curl -s -X POST http://localhost:5055/api/upload \
  -H "X-Session-ID: vision_session" \
  -F "file=@diagram.png")
FILENAME=$(echo $UPLOAD | jq -r '.filename')

# 2. Query visual contents
curl -X POST http://localhost:5055/api/generate \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: vision_session" \
  -d "{\"prompt\": \"Explain the flow shown in this diagram\", \"files\": [\"$FILENAME\"], \"stream\": false}"
```
