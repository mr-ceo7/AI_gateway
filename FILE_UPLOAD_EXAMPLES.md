# File & Multimodal Upload API Examples

This guide details file upload patterns across text, PDFs, and images with session isolation and prompt references.

---

## 1. Upload Patterns

### A. Multipart Upload (Text, PDF, Image)
```bash
curl -X POST http://localhost:5000/api/upload \
  -H "X-Session-ID: session_abc" \
  -F "file=@screenshot.png"
```

Response:
```json
{
  "context_mode": false,
  "extracted_txt": null,
  "filename": "screenshot_57c32242.png",
  "path": "/home/user/.gemini_uploads/session_abc/screenshot_57c32242.png",
  "session_id": "session_abc",
  "size": 22859,
  "success": true
}
```

### B. Base64 JSON Upload
```bash
curl -X POST http://localhost:5000/api/upload \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: session_abc" \
  -d '{
    "filename": "chart.png",
    "file": "'"$(base64 -w 0 chart.png)"'",
    "context_mode": false
  }'
```

---

## 2. Querying Uploaded Files

### Example 1: Image & Visual Analysis
```bash
# 1. Upload the image
UPLOAD=$(curl -s -X POST http://localhost:5000/api/upload \
  -H "X-Session-ID: visual_inspect" \
  -F "file=@ui_mockup.png")
IMG_NAME=$(echo $UPLOAD | jq -r '.filename')

# 2. Query visual contents
curl -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: visual_inspect" \
  -d '{
    "prompt": "List the UI components, colors, and layout structure in this image.",
    "files": ["'"$IMG_NAME"'"],
    "stream": false
  }'
```

### Example 2: PDF Document Query (Streaming)
```bash
# 1. Upload PDF (plain-text is automatically extracted)
UPLOAD=$(curl -s -X POST http://localhost:5000/api/upload \
  -H "X-Session-ID: doc_reader" \
  -F "file=@manual.pdf")
PDF_NAME=$(echo $UPLOAD | jq -r '.filename')

# 2. Query document with real-time SSE stream
curl -N -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: doc_reader" \
  -d '{
    "prompt": "Summarize chapter 3 safety protocols from this manual.",
    "files": ["'"$PDF_NAME"'"],
    "stream": true
  }'
```

### Example 3: Multiple Files (Comparing Data)
```bash
curl -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: sales_audit" \
  -d '{
    "prompt": "Compare Q1 and Q2 sales metrics and report deviations.",
    "files": [
      "q1_sales_a1b2c3d4.csv",
      "q2_sales_e5f6g7h8.csv"
    ],
    "stream": false
  }'
```

---

## 3. Python Integration Example

```python
import requests

BASE_URL = "http://localhost:5000"
SESSION_ID = "python_client_session"

def analyze_image(image_path: str, question: str):
    # 1. Upload the image
    with open(image_path, "rb") as f:
        upload_resp = requests.post(
            f"{BASE_URL}/api/upload",
            files={"file": (image_path, f, "image/png")},
            headers={"X-Session-ID": SESSION_ID}
        )
    upload_resp.raise_for_status()
    filename = upload_resp.json()["filename"]

    # 2. Query visual contents
    gen_resp = requests.post(
        f"{BASE_URL}/api/generate",
        json={
            "prompt": question,
            "files": [filename],
            "stream": False
        },
        headers={"X-Session-ID": SESSION_ID}
    )
    gen_resp.raise_for_status()
    return gen_resp.json()["response"]

if __name__ == "__main__":
    result = analyze_image("diagram.png", "Describe this diagram")
    print(result)
```

---

## 4. JavaScript / Browser Client Example

```javascript
const BASE_URL = "http://localhost:5000";
const SESSION_ID = "web_session_" + Date.now();

// 1. Upload File (Image or Document)
async function uploadFile(fileInput) {
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    const res = await fetch(`${BASE_URL}/api/upload`, {
        method: "POST",
        headers: { "X-Session-ID": SESSION_ID },
        body: formData
    });
    const data = await res.json();
    return data.filename;
}

// 2. Query with Streaming & Abort Support
async function askQuestionWithFile(filename, prompt) {
    const abortController = new AbortController();

    const res = await fetch(`${BASE_URL}/api/generate`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-Session-ID": SESSION_ID
        },
        body: JSON.stringify({
            prompt: prompt,
            files: [filename],
            stream: true
        }),
        signal: abortController.signal
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value);
        console.log("Chunk:", chunk);
    }
}
```

---

## 5. Storage & Isolation Architecture

1. **Storage Path:** Uploaded files are saved to `~/.gemini_uploads/<session_id>/<filename>`.
2. **File Naming:** Files are renamed to `<basename>_<hash8>.<ext>` to avoid namespace collisions.
3. **Execution Sandbox:** The `agy` subprocess runs with `cwd` set to `~/.gemini_uploads/<session_id>/`, providing read-only access to uploaded assets.
