# Client examples

Worked examples for calling the gateway from the shell, Python and JavaScript. See the [README](../README.md#api-reference) for the full API reference.

All examples assume the gateway listens on `localhost:5000` and that `TOKEN` holds one of the values from `GATEWAY_TOKENS` in the gateway's `.env`:

```bash
export TOKEN=$(grep '^GATEWAY_TOKENS=' ~/AI_gateway/.env | cut -d= -f2 | cut -d, -f1)
```

## Uploading

### Multipart (text, PDF, image)

```bash
curl -s http://localhost:5000/api/upload \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Session-ID: session_abc" \
  -F "file=@screenshot.png"
```

```json
{
  "context_mode": false,
  "extracted_txt": null,
  "filename": "screenshot_57c32242.png",
  "session_id": "session_abc",
  "size": 22859,
  "success": true
}
```

The stored name is `<name>_<first 8 hex digits of the MD5>.<ext>`. Use it in later `files` lists. For a PDF, `extracted_txt` names the plain-text copy the gateway made; you can keep referring to the PDF name.

### Base64 in JSON

```bash
curl -s http://localhost:5000/api/upload \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: session_abc" \
  -d '{
    "filename": "chart.png",
    "file": "'"$(base64 -w 0 chart.png)"'"
  }'
```

For JSON uploads the session comes from the `X-Session-ID` header only.

## Asking about files

### An image

```bash
IMG=$(curl -s http://localhost:5000/api/upload \
  -H "Authorization: Bearer $TOKEN" -H "X-Session-ID: visual" \
  -F "file=@ui_mockup.png" | jq -r .filename)

curl -s http://localhost:5000/api/generate \
  -H "Authorization: Bearer $TOKEN" -H "X-Session-ID: visual" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "List the UI components and the layout in this image.", "files": ["'"$IMG"'"]}'
```

### A PDF, streamed

```bash
PDF=$(curl -s http://localhost:5000/api/upload \
  -H "Authorization: Bearer $TOKEN" -H "X-Session-ID: reader" \
  -F "file=@manual.pdf" | jq -r .filename)

curl -sN http://localhost:5000/api/generate \
  -H "Authorization: Bearer $TOKEN" -H "X-Session-ID: reader" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Summarize chapter 3.", "files": ["'"$PDF"'"], "stream": true}'
```

### Several files

Files must have been uploaded to the same session:

```bash
curl -s http://localhost:5000/api/generate \
  -H "Authorization: Bearer $TOKEN" -H "X-Session-ID: audit" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Compare Q1 and Q2 and list the deviations.",
       "files": ["q1_sales_a1b2c3d4.csv", "q2_sales_e5f6a7b8.csv"]}'
```

A file name that doesn't exist in the session is skipped silently.

## Python

```python
import requests

BASE_URL = "http://localhost:5000"
HEADERS = {"Authorization": "Bearer YOUR_TOKEN", "X-Session-ID": "python_client"}


def ask_about_file(path: str, question: str) -> str:
    with open(path, "rb") as f:
        up = requests.post(f"{BASE_URL}/api/upload", files={"file": f}, headers=HEADERS)
    up.raise_for_status()

    resp = requests.post(
        f"{BASE_URL}/api/generate",
        json={"prompt": question, "files": [up.json()["filename"]]},
        headers=HEADERS,
        timeout=330,  # the gateway gives up after 300 s
    )
    resp.raise_for_status()
    return resp.json()["response"]


def stream(question: str):
    with requests.post(f"{BASE_URL}/api/generate", json={"prompt": question, "stream": True},
                       headers=HEADERS, stream=True) as resp:
        resp.raise_for_status()
        event = []
        for line in resp.iter_lines(decode_unicode=True):
            if line.startswith("data: "):
                event.append(line[6:])
            elif line == "" and event:  # blank line ends an event
                text = "\n".join(event)
                event = []
                if text == "[DONE]":
                    return
                if text.startswith("[SERVER]"):
                    continue
                if text.startswith("[ERROR]"):
                    raise RuntimeError(text)
                yield text


if __name__ == "__main__":
    print(ask_about_file("diagram.png", "Describe this diagram"))
    for chunk in stream("Explain recursion in two sentences"):
        print(chunk, end="", flush=True)
```

## JavaScript (browser or Node 18+)

Browsers on another origin also need that origin listed in `CORS_ORIGINS` (the default `*` allows any).

```javascript
const BASE_URL = "http://localhost:5000";
const HEADERS = { "Authorization": "Bearer YOUR_TOKEN", "X-Session-ID": "web_" + Date.now() };

async function upload(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE_URL}/api/upload`, { method: "POST", headers: HEADERS, body: form });
  return (await res.json()).filename;
}

// Streams the answer into onText(); call controller.abort() to stop (the gateway then kills the CLI).
async function ask(prompt, files, onText, controller = new AbortController()) {
  const res = await fetch(`${BASE_URL}/api/generate`, {
    method: "POST",
    headers: { ...HEADERS, "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, files, stream: true }),
    signal: controller.signal,
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {        // one SSE event
      const text = buffer.slice(0, end).split("\n")
        .filter(l => l.startsWith("data: ")).map(l => l.slice(6)).join("\n");
      buffer = buffer.slice(end + 2);
      if (text === "[DONE]") return;
      if (text.startsWith("[SERVER]")) continue;
      if (text.startsWith("[ERROR]")) throw new Error(text);
      onText(text);
    }
  }
}
```

## Where files go

1. Uploads are saved in `~/.gemini_uploads/<session_id>/`. The session ID is cleaned to letters, digits, `_` and `-`, at most 64 characters. The default session is `default`.
2. The CLI runs with that folder as its working directory, and the prompt lists the full paths of the files you referenced.
3. The prompt tells the agent it may only read the files. Only `agy` is also started with `--sandbox --disable-slash-commands`; `claude` and `copilot` run with their own default permissions.
4. Files older than `UPLOAD_TTL_SECONDS` (default 6 hours) are deleted the next time anyone uploads.
