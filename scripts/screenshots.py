"""Regenerate the screenshots in docs/.

Starts the gateway in a throwaway home with a stand-in `agy` (so no real account or
quota is used), drives the page in headless Chrome over the DevTools protocol and
saves PNGs, then records real curl calls against the API as an SVG. Needs
google-chrome (or chromium), curl, and `pip install websocket-client rich`.

    python scripts/screenshots.py
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
TOKEN = "demo-token"

FAKE_AGY = r'''#!/usr/bin/env python3
"""Stand-in for agy that answers like the real CLI's json / stream-json output."""
import json, sys, time
args = sys.argv[1:]
if "-p" not in args:
    sys.exit(0)
prompt = args[args.index("-p") + 1]
fmt = args[args.index("--output-format") + 1] if "--output-format" in args else "text"
if "AVAILABLE FILES" in prompt:
    answer = ("The report covers **Q3 sales** for three regions.\n\n"
              "| Region | Revenue | Change |\n|---|---|---|\n"
              "| East | $1.24M | +8% |\n| West | $0.97M | -3% |\n| North | $0.66M | +15% |\n\n"
              "North grew fastest. West is the only region that shrank.")
elif "15 * 12" in prompt:
    answer = "15 x 12 = **180**"
else:
    answer = ("Here is a version that ignores case, spaces and punctuation:\n\n"
              "```python\nimport re\n\ndef is_palindrome(text: str) -> bool:\n"
              "    cleaned = re.sub(r\"[^a-z0-9]\", \"\", text.lower())\n"
              "    return cleaned == cleaned[::-1]\n\n\n"
              "print(is_palindrome(\"A man, a plan, a canal: Panama\"))  # True\n```\n\n"
              "It keeps only letters and digits, then compares the string with its reverse.")
if fmt == "stream-json":
    print(json.dumps({"event": "init", "conversation_id": "demo"}), flush=True)
    for i in range(0, len(answer), 24):
        print(json.dumps({"event": "step_update", "step_update": {"text_delta": answer[i:i+24]}}), flush=True)
        time.sleep(0.02)
    print(json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": answer}}), flush=True)
else:
    print(json.dumps({"result": {"status": "SUCCESS", "response": answer}}))
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 20) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"{url} did not come up")


class Chrome:
    def __init__(self, width: int, height: int, mobile: bool = False) -> None:
        exe = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        if not exe:
            raise SystemExit("google-chrome or chromium is required")
        self.port = free_port()
        self.profile = tempfile.mkdtemp(prefix="gw-chrome-")
        self.proc = subprocess.Popen(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
             f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}",
             f"--window-size={width},{height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_http(f"http://127.0.0.1:{self.port}/json/version")
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json"))
        page = next(t for t in tabs if t["type"] == "page")
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
        self.next_id = 0
        self.call("Page.enable")
        if mobile:
            self.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                      deviceScaleFactor=2, mobile=True)

    def call(self, method: str, **params):
        self.next_id += 1
        self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str):
        res = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return res.get("result", {}).get("value")

    def wait_for(self, expr: str, timeout: float = 20) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.js(expr):
                return
            time.sleep(0.2)
        raise RuntimeError(f"timed out waiting for: {expr}")

    def goto(self, url: str) -> None:
        self.call("Page.navigate", url=url)
        self.wait_for("document.readyState === 'complete'")

    def shot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", format="png")["data"]
        path.write_bytes(base64.b64decode(data))
        print(f"wrote {path.relative_to(ROOT)}")

    def close(self) -> None:
        self.ws.close()
        self.proc.terminate()
        self.proc.wait(timeout=5)
        shutil.rmtree(self.profile, ignore_errors=True)


def ask(chrome: Chrome, text: str) -> None:
    chrome.js(f"""(() => {{
        const box = document.getElementById('user-input');
        box.value = {json.dumps(text)};
        autoResize(box);
        handleSendOrStop();
    }})()""")
    # the answer is complete once the send button is back (stop icon hidden)
    time.sleep(0.5)
    chrome.wait_for("document.getElementById('stop-icon').style.display === 'none'", timeout=30)
    time.sleep(0.6)


def terminal_svg(path: Path, title: str, commands: list[tuple[str, list[str]]]) -> None:
    """Run each command and save the shown command lines plus real output as an SVG."""
    from rich.console import Console
    from rich.text import Text

    console = Console(record=True, width=100, force_terminal=True, file=open(os.devnull, "w"))
    for i, (shown, argv) in enumerate(commands):
        if i:
            console.print()
        for j, part in enumerate(shown.split("\n")):
            line = Text("$ " if j == 0 else "  ", style="bold #6a9955")
            line.append(part, style="bold #e6e6e6")
            console.print(line)
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60).stdout
        console.print(Text(out.rstrip("\n"), style="#c8ccd4"))
    path.write_text(console.export_svg(title=title))
    print(f"wrote {path.relative_to(ROOT)}")


def api_shot(api: str, csv: Path) -> None:
    auth = f"Authorization: Bearer {TOKEN}"
    json_hdr = "Content-Type: application/json"
    upload = ["curl", "-s", api + "upload", "-H", auth, "-H", "X-Session-ID: demo", "-F", f"file=@{csv}"]
    name = json.loads(subprocess.run(upload, capture_output=True, text=True).stdout)["filename"]
    ask_file = json.dumps({"prompt": "Summarize this report", "files": [name]})
    terminal_svg(DOCS / "api-curl.svg", "AI Gateway API", [
        ("curl -sN localhost:5000/api/generate -H \"Authorization: Bearer $TOKEN\" \\\n"
         "     -H 'Content-Type: application/json' -d '{\"prompt\": \"Calculate 15 * 12\", \"stream\": true}'",
         ["curl", "-sN", api + "generate", "-H", auth, "-H", json_hdr,
          "-d", '{"prompt": "Calculate 15 * 12", "stream": true}']),
        ("curl -s localhost:5000/api/upload -H \"Authorization: Bearer $TOKEN\" \\\n"
         "     -H 'X-Session-ID: demo' -F file=@q3_sales.csv",
         upload),
        ("curl -s localhost:5000/api/generate -H \"Authorization: Bearer $TOKEN\" \\\n"
         "     -H 'X-Session-ID: demo' -H 'Content-Type: application/json' \\\n"
         f"     -d '{ask_file}'",
         ["curl", "-s", api + "generate", "-H", auth, "-H", "X-Session-ID: demo", "-H", json_hdr,
          "-d", ask_file]),
        ("curl -s -o /dev/null -w '%{http_code}\\n' localhost:5000/api/accounts    # no token",
         ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}\n", api + "accounts"]),
    ])


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="gw-demo-"))
    home, bindir = work / "home", work / "bin"
    (home / ".gemini").mkdir(parents=True)
    bindir.mkdir()
    # credentials present, so the gateway doesn't start the interactive login flow
    (home / ".gemini" / "oauth_creds.json").write_text('{"access_token": "demo"}')
    (bindir / "agy").write_text(FAKE_AGY)
    (bindir / "agy").chmod(0o755)
    csv = work / "q3_sales.csv"
    csv.write_text("region,revenue,change\nEast,1240000,+8%\nWest,970000,-3%\nNorth,660000,+15%\n")

    # run a copy of the app so the repository's own .env (real tokens, backend) is not loaded
    app_dir = work / "app"
    app_dir.mkdir()
    shutil.copy2(ROOT / "app.py", app_dir)
    shutil.copytree(ROOT / "utils", app_dir / "utils")
    shutil.copytree(ROOT / "templates", app_dir / "templates")

    port = free_port()
    env = {**os.environ, "HOME": str(home), "PATH": f"{bindir}:{os.environ['PATH']}",
           "GATEWAY_TOKENS": TOKEN, "PORT": str(port)}
    server = subprocess.Popen([sys.executable, "app.py"], cwd=app_dir, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    DOCS.mkdir(exist_ok=True)
    try:
        wait_http(url + "api/auth/status")

        chrome = Chrome(1440, 900)
        chrome.goto(url)
        chrome.js(f"localStorage.setItem('gateway_token', {json.dumps(TOKEN)})")
        chrome.goto(url)
        time.sleep(2.5)  # splash animation
        chrome.shot(DOCS / "web-welcome.png")
        ask(chrome, "Write a Python function that checks whether a string is a palindrome")
        chrome.shot(DOCS / "web-chat.png")

        # attach a file through the hidden file input, then ask about it
        chrome.js("startNewChat()")
        doc = chrome.call("DOM.getDocument")
        node = chrome.call("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#file-input")
        chrome.call("DOM.setFileInputFiles", nodeId=node["nodeId"], files=[str(csv)])
        chrome.wait_for("typeof uploadedFiles !== 'undefined' && uploadedFiles.length > 0")
        ask(chrome, "Summarize this report")
        chrome.shot(DOCS / "web-files.png")
        chrome.close()

        phone = Chrome(390, 844, mobile=True)
        phone.goto(url)
        phone.js(f"localStorage.setItem('gateway_token', {json.dumps(TOKEN)})")
        phone.goto(url)
        time.sleep(2.5)
        ask(phone, "Write a Python function that checks whether a string is a palindrome")
        phone.shot(DOCS / "web-mobile.png")
        phone.close()

        api_shot(url + "api/", csv)
    finally:
        server.terminate()
        server.wait(timeout=5)
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
