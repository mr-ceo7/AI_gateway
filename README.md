# AI Gateway API

This project provides a RESTful API for uploading files (including PDFs), extracting their content, and querying them using the Gemini CLI. It is designed to be easily integrated into your own client applications.

## Features
- Upload text or PDF files for analysis
- Automatic PDF-to-text extraction (requires PyPDF2)
- Stateless and context-aware file management
- Query uploaded files using natural language prompts
- Streaming and non-streaming response modes
- Real-time progress updates for long-running requests

## API Endpoints

### 1. File Upload
`POST /api/upload`

- **Form-data:**
  - `file`: The file to upload (text or PDF)
  - `context_mode` (optional): `true` to keep file for session context, `false` (default) for stateless
- **Headers:**
  - `X-Session-ID` (optional): Unique session identifier for context mode
- **Response:**
  - `filename`: The stored filename
  - `extracted_txt`: Name of extracted text file (for PDFs)
  - `size`: File size in bytes
  - `context_mode`: Whether context mode is enabled

**Example (cURL):**
```bash
curl -X POST http://localhost:5000/api/upload -F "file=@mydoc.pdf"
```

### 2. Generate Answer
`POST /api/generate`

- **JSON Body:**
  - `prompt`: Your question or instruction
  - `files`: List of uploaded filenames to reference
  - `stream` (optional): `true` for streaming response, `false` (default) for standard
- **Response:**
  - `response`: The answer from Gemini CLI (non-streaming)
  - Streaming: Server-Sent Events (SSE) with progress and output

**Example (cURL):**
```bash
curl -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Summarize this file", "files": ["mydoc_1234abcd.txt"]}'
```

### 3. Authentication (if required)
- The API manages Gemini CLI authentication automatically. If manual authentication is needed, use:
  - `GET /api/auth/status`
  - `GET /api/auth/url`
  - `POST /api/auth/submit` (with `{ "code": "..." }`)

## Client Integration Tips
- Always upload files before referencing them in prompts.
- For PDFs, use the `extracted_txt` field in the upload response for best results.
- Use `stream: true` for large files or long queries to receive real-time updates.
- Handle error messages and progress updates in your client UI.
- Use unique `X-Session-ID` headers for context mode if you want files to persist across multiple requests.

## Requirements
- Python 3.8+
- Flask
- PyPDF2 (for PDF extraction)
- Gemini CLI (installed and authenticated)

## Running the Server
```bash
pip install -r requirements.txt
python app.py
```

## Example Workflow
1. Upload a file:
   ```bash
   curl -X POST http://localhost:5000/api/upload -F "file=@example.pdf"
   ```
2. Use the returned filename (or extracted text filename) in your prompt:
   ```bash
   curl -X POST http://localhost:5000/api/generate \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Summarize the document", "files": ["example_1234abcd.txt"]}'
   ```

## Notes
- Uploaded files are stored in `~/.gemini_uploads/`.
- The API enforces strict read-only access for Gemini CLI.
- For best results, keep files small or use streaming mode for large documents.

---
For questions or issues, please contact the project maintainer.
