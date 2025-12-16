# File Upload API Examples

## Upload Endpoint

### Upload via multipart/form-data

```bash
curl -X POST http://localhost:5000/api/upload \
  -F "file=@document.txt"
```

Response:
```json
{
  "success": true,
  "filename": "document_a1b2c3d4.txt",
  "size": 1024,
  "path": "/home/user/.gemini_uploads/document_a1b2c3d4.txt"
}
```

### Upload via JSON with base64

```bash
curl -X POST http://localhost:5000/api/upload \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "data.csv",
    "file": "'"$(base64 -w 0 data.csv)"'"
  }'
```

## Generate with Files

### Example 1: Analyze a single file

```bash
# 1. Upload file
curl -X POST http://localhost:5000/api/upload -F "file=@report.txt"

# 2. Generate with file reference
curl -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Summarize the main points from this report",
    "files": ["report_a1b2c3d4.txt"],
    "stream": false
  }'
```

### Example 2: Multiple files

```bash
curl -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Compare the data in both CSV files and identify trends",
    "files": [
      {"filename": "sales_2023.csv"},
      {"filename": "sales_2024.csv"}
    ],
    "stream": true
  }'
```

### Example 3: Using messages with files

```bash
curl -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "What does this code do?"}
    ],
    "files": ["script.py"],
    "stream": false
  }'
```

## Python Client Example

```python
import requests
import base64

# Upload file
with open('document.pdf', 'rb') as f:
    response = requests.post('http://localhost:5000/api/upload', 
                           files={'file': f})
    file_info = response.json()
    print(f"Uploaded: {file_info['filename']}")

# Generate with file
response = requests.post('http://localhost:5000/api/generate',
    json={
        'prompt': 'Extract key information from this document',
        'files': [file_info['filename']],
        'stream': False
    })

print(response.json()['response'])
```

## JavaScript Client Example

```javascript
// Upload file
const formData = new FormData();
formData.append('file', fileInput.files[0]);

const uploadResponse = await fetch('/api/upload', {
    method: 'POST',
    body: formData
});
const fileInfo = await uploadResponse.json();

// Generate with file
const generateResponse = await fetch('/api/generate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
        prompt: 'Analyze this file',
        files: [fileInfo.filename],
        stream: false
    })
});

const result = await generateResponse.json();
console.log(result.response);
```

## How It Works

1. **Upload**: Files are saved to `~/.gemini_uploads/` with unique names (hash-based to prevent collisions)
2. **Reference**: Include uploaded filenames in the `files` array when calling `/api/generate`
3. **System Prompt**: The API automatically prepends instructions telling the AI to read the specified files
4. **Working Directory**: Gemini CLI runs with `~/.gemini_uploads/` as the working directory, so it can access the files

## File Naming

- Original filename: `document.txt`
- Stored as: `document_a1b2c3d4.txt` (hash suffix prevents collisions)
- Use the returned `filename` from the upload response in subsequent generate calls
