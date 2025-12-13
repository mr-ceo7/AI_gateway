from flask import Flask, request, jsonify, render_template
import subprocess
import os
import threading
import time
import re

app = Flask(__name__)

# Global state for authentication
class GeminiAuthenticator:
    def __init__(self):
        self.auth_process = None
        self.auth_url = None
        self.is_authenticated = False
        self.auth_lock = threading.Lock()

    def check_auth_status(self):
        """Checks if currently authenticated by running a quick command."""
        try:
            # mimic the check in start.sh
            result = subprocess.run(
                ['gemini', 'chat', 'Hello'],
                capture_output=True,
                text=True,
                check=False,
                env={'TERM': 'dumb', **os.environ} # Ensure correct env
            )
            self.is_authenticated = (result.returncode == 0)
            return self.is_authenticated
        except Exception:
            self.is_authenticated = False
            return False

    def start_auth_flow(self):
        """Starts the interactive auth process and scrapes the URL."""
        with self.auth_lock:
            if self.auth_process and self.auth_process.poll() is None:
                return # Already running

            env = os.environ.copy()
            env['TERM'] = 'dumb'
            env['NO_BROWSER'] = 'true'
            
            env = os.environ.copy()
            env['TERM'] = 'dumb'
            env['NO_BROWSER'] = 'true'
            
            # Start 'gemini "hello"' to force implicit auth check
            # We merge stderr into stdout to catch the URL
            self.auth_process = subprocess.Popen(
                ['gemini', 'hello'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0, # Unbuffered
                env=env
            )
            
            # Start a background thread to read output and find the URL
            threading.Thread(target=self._monitor_output, daemon=True).start()

    def _monitor_output(self):
        """Reads stdout looking for the auth URL."""
        if not self.auth_process:
            return

        print("Auth Monitor: Started reading output...", flush=True)
        # Relaxed regex to catch any https link, as CLI output might vary
        url_pattern = re.compile(r'(https://[^\s]+)')
        
        while True:
            line = self.auth_process.stdout.readline()
            if not line:
                break
            
            print(f"Auth Output: {line.strip()}", flush=True)
            
            # Check for URL
            match = url_pattern.search(line)
            if match:
                self.auth_url = match.group(1)
                print(f"Auth Monitor: Found URL: {self.auth_url}", flush=True)
                # We can stop reading aggressively, but we need to keep reading 
                # so the buffer doesn't fill up?
                # Actually, after URL, it waits for input.
            
            # If we successfully caught the URL, we can stop scanning aggressively
            if self.auth_url and "Enter the authorization code" in line:
                pass # Ready for input

    def submit_code(self, code):
        """Writes the auth code to the process stdin."""
        with self.auth_lock:
            if not self.auth_process or self.auth_process.poll() is not None:
                return False, "Auth process not running."
            
            try:
                print(f"Auth Monitor: Submitting code (len={len(code)})...", flush=True)
                if not code.endswith('\n'):
                    code += '\n'
                
                self.auth_process.stdin.write(code)
                self.auth_process.stdin.flush()
                self.auth_process.stdin.close() # Usually closing stdin triggers processing
                
                # Wait a bit for it to finish and exit
                self.auth_process.wait(timeout=10)
                
                if self.auth_process.returncode == 0:
                    self.is_authenticated = True
                    self.auth_url = None
                    self.auth_process = None
                    return True, "Authentication successful."
                else:
                    return False, "Authentication failed (process exited with error)."
            except Exception as e:
                print(f"Auth Monitor: Exception submitting code: {e}", flush=True)
                return False, f"Error submitting code: {str(e)}"

authenticator = GeminiAuthenticator()

# Initialize check on startup
authenticator.check_auth_status()
if not authenticator.is_authenticated:
    print("Initial Auth Check Failed. Starting Auth Flow...", flush=True)
    authenticator.start_auth_flow()
else:
    print("Initial Auth Check Passed.", flush=True)


@app.route('/api/auth/status')
def auth_status():
    status = {
        'authenticated': authenticator.is_authenticated,
        'has_url': bool(authenticator.auth_url)
    }
    return jsonify(status)

@app.route('/api/auth/url')
def get_auth_url():
    if authenticator.auth_url:
        return jsonify({'url': authenticator.auth_url})
    return jsonify({'url': None}), 404

@app.route('/api/auth/submit', methods=['POST'])
def submit_auth_code():
    data = request.json
    code = data.get('code')
    if not code:
        return jsonify({'error': 'Code required'}), 400
    
    success, message = authenticator.submit_code(code)
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': message}), 400



@app.route('/')
def home():
    return render_template('index.html')


@app.route('/api/generate', methods=['POST'])
def generate():
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'Missing request body'}), 400

    prompt = ""
    if 'messages' in data:
        # Context Mode: Construct prompt from history
        for msg in data['messages']:
            role = "User" if msg['role'] == 'user' else "Model"
            prompt += f"{role}: {msg['content']}\n"
    elif 'prompt' in data:
        # Stateless Mode
        prompt = data['prompt']
    else:
        return jsonify({'error': 'Missing prompt or messages'}), 400

    stream = data.get('stream', False)
    # Add debug logging
    print(f"Generating with prompt length: {len(prompt)}", flush=True)
    
    # Set up environment - mimic simple shell
    env = os.environ.copy()
    env['TERM'] = 'dumb' # Force non-interactive

    if stream:
        def generate_output():
            try:
                # Use ['gemini', 'chat', prompt] which is the robust way to send a message
                process = subprocess.Popen(
                    ['gemini', 'chat', prompt],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, # Merge stderr
                    text=True,
                    bufsize=0, # Unbuffered
                    env=env
                )
                
                print(f"Started gemini chat with prompt arg (len={len(prompt)}). Reading stdout...", flush=True)

                while True:
                    # Read larger chunks to avoid overhead, but small enough for streaming
                    # Note: read(1) is fine for text=True unbuffered
                    chunk = process.stdout.read(1)
                    if not chunk and process.poll() is not None:
                        break
                    if chunk:
                        yield chunk
                
                if process.returncode != 0:
                     print(f"Process exited with code {process.returncode}", flush=True)

            except Exception as e:
                print(f"Exception during generation: {e}", flush=True)
                yield f"\n[Exception: {str(e)}]"

        return app.response_class(generate_output(), mimetype='text/plain')

    else:
        try:
            print("Running subprocess.run (gemini chat prompt)...", flush=True)
            result = subprocess.run(
                ['gemini', 'chat', prompt],
                capture_output=True,
                text=True,
                check=False,
                env=env
            )
            
            print(f"Subprocess finished. Code: {result.returncode}", flush=True)
            if result.returncode != 0:
                 print(f"Error output: {result.stderr}", flush=True)
                 return jsonify({
                     'error': 'Gemini CLI failed',
                     'stderr': result.stderr
                 }), 500
                 
            return jsonify({'response': result.stdout.strip()})
    
        except Exception as e:
            print(f"Exception: {e}", flush=True)
            return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
