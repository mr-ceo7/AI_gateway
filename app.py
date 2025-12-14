from flask import Flask, request, jsonify, render_template
import subprocess
import os
import threading
import time
import re
import pty
import fcntl

app = Flask(__name__)

# Helper to clean Gemini CLI noisy output
def clean_gemini_output(text, prompt=None):
    # Strip ANSI escape codes
    ansi_pattern = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_pattern.sub('', text or '')

    lines = text.splitlines()
    cleaned_lines = []
    removed_prompt = False

    for line in lines:
        l = line.strip()
        # Filter known noisy lines
        if l.startswith('[STARTUP]'):
            continue
        if l.startswith('Loaded cached credentials'):
            continue
        if l == "Hello! I'm ready for your first command.":
            continue
        if l.startswith('Welcome to Gemini'):
            continue
        # Remove the echoed user prompt once
        if prompt and not removed_prompt and l == prompt.strip():
            removed_prompt = True
            continue
        cleaned_lines.append(line)

    return '\n'.join(cleaned_lines).strip()

# Global state for authentication
class GeminiAuthenticator:
    def __init__(self):
        self.auth_process = None
        self.auth_url = None
        self.is_authenticated = False
        self.auth_lock = threading.Lock()
        self.credentials_path = None

    def check_auth_status(self):
        """Checks authentication by inspecting credentials instead of probing the CLI."""
        # Check for Gemini CLI credentials at ~/.gemini/oauth_creds.json
        try:
            home = os.path.expanduser('~')
            
            # Primary: Check for oauth_creds.json with access/refresh tokens
            oauth_creds_path = os.path.join(home, '.gemini', 'oauth_creds.json')
            settings_path = os.path.join(home, '.gemini', 'settings.json')
            accounts_path = os.path.join(home, '.gemini', 'google_accounts.json')
            
            # Check oauth_creds.json for tokens
            try:
                if os.path.isfile(oauth_creds_path):
                    import json
                    with open(oauth_creds_path, 'r', encoding='utf-8') as f:
                        data = json.loads(f.read().strip())
                    # Check for OAuth tokens
                    if any(k in data for k in {'access_token', 'refresh_token'}):
                        self.is_authenticated = True
                        self.credentials_path = oauth_creds_path
                        return True
            except Exception:
                pass
            
            # Fallback: Check for settings.json + active account in google_accounts.json
            try:
                if os.path.isfile(settings_path) and os.path.isfile(accounts_path):
                    import json
                    with open(accounts_path, 'r', encoding='utf-8') as f:
                        accounts_data = json.loads(f.read().strip())
                    # If there's an active account, likely authenticated
                    if 'active' in accounts_data and accounts_data['active']:
                        self.is_authenticated = True
                        self.credentials_path = accounts_path
                        return True
            except Exception:
                pass
            
        except Exception:
            pass
        
        self.is_authenticated = False
        self.credentials_path = None
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
        
        # Set PTY to non-blocking mode immediately
        try:
            flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except (OSError, ValueError):
            return
        
        # Regex to catch the URL
        url_pattern = re.compile(r'(https://[^\s]+)')
        # Regex to strip ANSI codes
        ansi_include_pattern = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        
        # State to track if we've handled the initial menu
        menu_handled = False

        while True:
            try:
                # Check if fd is still valid before reading
                if not hasattr(self, 'master_fd') or self.master_fd is None:
                    break
                
                # Non-blocking read from PTY
                try:
                    output = os.read(self.master_fd, 1024).decode('utf-8', errors='ignore')
                except (OSError, BlockingIOError):
                    # No data available, wait and retry
                    time.sleep(0.1)
                    continue
                
                if not output:
                    # PTY closed or no more output
                    break
                
                # Print raw for debugging
                for line in output.splitlines():
                    print(f"Auth Output: {line}", flush=True)
                    
                    # Strip ANSI
                    clean_line = ansi_include_pattern.sub('', line)

                    # Check for Menu and auto-select "Login with Google" (Option 1)
                    if not menu_handled and "Login with Google" in clean_line:
                        print("Auth Monitor: Detecting Auth Menu. Selecting option 1...", flush=True)
                        time.sleep(1) # Small buffer
                        try:
                            os.write(self.master_fd, b'1\n') # Send '1' and Enter
                            menu_handled = True
                        except (OSError, ValueError):
                            # fd might be closed
                            break
                    
                    # Check for URL
                    match = url_pattern.search(clean_line)
                    if match:
                        found_url = match.group(1)
                        # Filter out internal links if needed, but capturing 'https://...' is good start
                        if "google.com" in found_url or "accounts" in found_url:
                             self.auth_url = found_url
                             print(f"Auth Monitor: Found URL: {self.auth_url}", flush=True)
                    
                    if "Tips for getting started" in clean_line or "Welcome to Gemini" in clean_line:
                        # NOTE: The CLI sometimes prints this even when NOT authenticated (in the banner),
                        # so we cannot rely on it for success. We will rely on submit_code polling check_auth_status.
                        pass

            except (OSError, TypeError, ValueError):
                # This happens when the PTY is closed or master_fd is None
                break
            except Exception as e:
                print(f"Auth Monitor: Error reading PTY output: {e}", flush=True)
                break
        print("Auth Monitor: PTY output monitoring finished.", flush=True)

    def _cleanup_process(self):
        """Helper to kill the auth process and close fds."""
        # Close fd FIRST before killing process to avoid fd use-after-free
        if hasattr(self, 'master_fd') and self.master_fd:
            try:
                os.close(self.master_fd)
            except:
                pass
            self.master_fd = None
        
        # Now kill the process
        if self.auth_process:
            # Try to exit gracefully first to allow saving state
            try:
                self.auth_process.terminate()
                self.auth_process.wait(timeout=2)
            except:
                try:
                    self.auth_process.kill()
                except:
                    pass
            self.auth_process = None

    def submit_code(self, code):
        """Writes the auth code to the PTY and waits for success indicators."""
        try:
            # Write code under lock
            with self.auth_lock:
                if not self.auth_process or self.auth_process.poll() is not None:
                    return False, "Auth process not running."
                if self.master_fd is None:
                    return False, "PTY master not open."
                
                print(f"Auth Monitor: Submitting code (len={len(code)})...", flush=True)
                if not code.endswith('\n'):
                    code += '\n'
                
                # Write code to the PTY master (which sends it to subprocess stdin)
                try:
                    os.write(self.master_fd, code.encode('utf-8'))
                except (OSError, ValueError) as e:
                    print(f"Auth Monitor: Error writing code to PTY: {e}", flush=True)
                    return False, f"Error writing to PTY: {str(e)}"
                
                # Set PTY to non-blocking mode for reading
                try:
                    flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
                    fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                except (OSError, ValueError) as e:
                    print(f"Auth Monitor: Error setting PTY non-blocking: {e}", flush=True)
                    return False, f"Error configuring PTY: {str(e)}"
            
            # Release lock before waiting (don't hold lock during long polling)
            print("Auth Monitor: Code submitted. Waiting for auth success indicators...", flush=True)
            success_indicators = {'sandbox', '/app', 'auto'}
            timeout = 30  # Max 30 seconds to see success indicator
            start_time = time.time()
            ansi_pattern = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            
            while time.time() - start_time < timeout:
                try:
                    # Non-blocking read from PTY
                    if not hasattr(self, 'master_fd') or self.master_fd is None:
                        break
                    
                    output = os.read(self.master_fd, 1024).decode('utf-8', errors='ignore')
                    if output:
                        # Strip ANSI codes for cleaner matching
                        clean_output = ansi_pattern.sub('', output)
                        print(f"Auth Monitor: PTY Output: {clean_output}", flush=True)
                        
                        # Check for any success indicator
                        if any(indicator in clean_output for indicator in success_indicators):
                            print(f"Auth Monitor: Detected success indicator in output. Auth Successful.", flush=True)
                            # Brief pause for final processing
                            time.sleep(1)
                            with self.auth_lock:
                                self._cleanup_process()
                            return True, "Authentication successful."
                except (OSError, BlockingIOError, ValueError):
                    # PTY would block, no data available yet, or fd closed
                    pass
                
                time.sleep(0.5)  # Check every 500ms
            
            # Timeout reached, check credentials file as fallback
            print("Auth Monitor: Timeout waiting for success indicator, checking credentials file...", flush=True)
            if self.check_auth_status():
                print("Auth Monitor: Credentials verified via file. Auth Successful.", flush=True)
                with self.auth_lock:
                    self._cleanup_process()
                return True, "Authentication successful."
            
            # If still not successful, let client poll
            print("Auth Monitor: Code accepted, verification pending...", flush=True)
            return True, "Code submitted. Verifying..."

        except Exception as e:
            print(f"Auth Monitor: Exception submitting code: {e}", flush=True)
            try:
                with self.auth_lock:
                    self._cleanup_process()
            except:
                pass
            return False, f"Error submitting code: {str(e)}"

    def force_terminate(self):
        """Manually kills the auth process and checks status."""
        with self.auth_lock:
            print("Auth Monitor: Force terminating auth process...", flush=True)
            self._cleanup_process()
            # Check if we are authenticated now
            is_authed = self.check_auth_status()
            if is_authed:
                return True, "Process terminated. Authentication successful."
            else:
                return False, "Process terminated. Authentication failed."

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

@app.route('/api/auth/terminate', methods=['POST'])
def auth_terminate():
    success, message = authenticator.force_terminate()
    return jsonify({
        'success': success,
        'message': message
    })



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
    env['NO_BROWSER'] = 'true'

    if stream:
        def generate_output():
            try:
                # Use ['gemini'] (REPL) and pipe prompt to stdin
                # This mimics 'echo "prompt" | gemini' which is proven to work
                # mocking start.sh behavior.
                process = subprocess.Popen(
                    ['gemini'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, # Merge stderr
                    text=True,
                    bufsize=0, # Unbuffered
                    env=env
                )
                
                print(f"Started gemini REPL with stdin pipe (len={len(prompt)}). Writing prompt...", flush=True)
                
                # Write prompt and close stdin to signal EOF
                # This should make 'gemini' process the input and exit
                try:
                    process.stdin.write(prompt)
                    process.stdin.close()
                except Exception as e:
                     print(f"Error writing to stdin: {e}", flush=True)
                     return

                print("Prompt written. Reading stdout...", flush=True)
                buffer = ""
                while True:
                    # Read small chunks for responsive streaming
                    chunk = process.stdout.read(1)
                    if not chunk and process.poll() is not None:
                        # Flush remaining buffer
                        if buffer:
                            cleaned_tail = clean_gemini_output(buffer, prompt)
                            if cleaned_tail:
                                yield cleaned_tail
                        break
                    if chunk:
                        buffer += chunk
                        # Emit complete lines after cleaning
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            cleaned_line = clean_gemini_output(line, prompt)
                            if cleaned_line:
                                yield cleaned_line + "\n"
                
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
                 
            cleaned = clean_gemini_output(result.stdout, prompt)
            return jsonify({'response': cleaned})
    
        except Exception as e:
            print(f"Exception: {e}", flush=True)
            return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
