from flask import Flask, request, jsonify, render_template
import subprocess
import os
import threading
import time
import re
import pty

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
            env['TERM'] = 'xterm-256color' # Pretend to be a real terminal
            env['NO_BROWSER'] = 'true'
            
            # Create a pseudo-terminal
            master_fd, slave_fd = pty.openpty()
            
            # Start 'gemini' (interactive REPL) to trigger auth flow
            # We connect stdout/stdin to the PTY
            self.auth_process = subprocess.Popen(
                ['gemini'],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd, # Merge all output to PTY
                text=True,
                bufsize=0,
                env=env,
                close_fds=True
            )
            
            # Close slave_fd in parent (the child has it now)
            os.close(slave_fd)
            self.master_fd = master_fd # Save master for reading/writing
            
            # Start a background thread to read output from the PTY
            threading.Thread(target=self._monitor_output, daemon=True).start()

    def _monitor_output(self):
        """Reads PTY output looking for the auth URL."""
        if not self.auth_process or not hasattr(self, 'master_fd') or self.master_fd is None:
            return

        print("Auth Monitor: Started reading PTY output...", flush=True)
        # Regex to catch the URL
        url_pattern = re.compile(r'(https://[^\s]+)')
        # Regex to strip ANSI codes
        ansi_include_pattern = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        
        # State to track if we've handled the initial menu
        menu_handled = False

        while True:
            try:
                # Read from PTY
                output = os.read(self.master_fd, 1024).decode('utf-8', errors='ignore')
                if not output:
                    # PTY closed or no more output
                    break
                
                # Print raw for debugging
                for line in output.splitlines():
                    print(f"Auth Output: {line}", flush=True)
                    
                    # Check for Menu and auto-select "Login with Google" (Option 1)
                    if not menu_handled and "Login with Google" in clean_line:
                        print("Auth Monitor: Detecting Auth Menu. Selecting option 1...", flush=True)
                        time.sleep(1) # Small buffer
                        os.write(self.master_fd, b'1\n') # Send '1' and Enter
                        menu_handled = True

                    # Check for URL
                    match = url_pattern.search(clean_line)
                    if match:
                        found_url = match.group(1)
                        # Filter out internal links if needed, but capturing 'https://...' is good start
                        if "google.com" in found_url or "accounts" in found_url:
                             self.auth_url = found_url
                             print(f"Auth Monitor: Found URL: {self.auth_url}", flush=True)
                    
                    # Check for Success Indicator
                    if "Tips for getting started" in clean_line or "Welcome to Gemini" in clean_line:
                        print("Auth Monitor: Auth Success Detected!", flush=True)
                        self.is_authenticated = True
                        self.auth_url = None
                        # We can close the process now as we only needed to auth
                        # self.auth_process.terminate() # Actually, let's leave it to submit_code cleanup or just running

            except OSError:
                # This happens when the PTY is closed by the child process exiting
                break
            except Exception as e:
                print(f"Auth Monitor: Error reading PTY output: {e}", flush=True)
                break
        print("Auth Monitor: PTY output monitoring finished.", flush=True)


    def submit_code(self, code):
        """Writes the auth code to the PTY."""
        with self.auth_lock:
            if not self.auth_process or self.auth_process.poll() is not None:
                return False, "Auth process not running."
            if self.master_fd is None:
                return False, "PTY master not open."
            
            try:
                print(f"Auth Monitor: Submitting code (len={len(code)})...", flush=True)
                if not code.endswith('\n'):
                    code += '\n'
                
                # Write code to the PTY master (which sends it to subprocess stdin)
                os.write(self.master_fd, code.encode('utf-8'))
                
                # Wait for authentication success signal
                print("Auth Monitor: Waiting for success signal...", flush=True)
                for _ in range(20): # Wait up to 20 seconds
                    if self.is_authenticated:
                        print("Auth Monitor: Success signal received!", flush=True)
                        # Clean up
                        try:
                            self.auth_process.terminate()
                            self.auth_process.wait(timeout=2)
                        except:
                            pass
                        if self.master_fd:
                            try:
                                os.close(self.master_fd)
                            except:
                                pass
                            self.master_fd = None
                        self.auth_process = None
                        return True, "Authentication successful."
                    time.sleep(1)
                
                return False, "Authentication timed out (success signal not received)."

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
