from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import subprocess
import os
import threading
import time
import re
import pty
import fcntl
import base64
import hashlib
from werkzeug.utils import secure_filename
try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

app = Flask(__name__)

# Enable CORS for localhost:3000
CORS(app, resources={
    r"/api/*": {
        "origins": ["http://localhost:3000", "http://localhost:3001", "http://192.168.1.192:3000"],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# Directory for uploaded files
UPLOAD_DIR = os.path.join(os.path.expanduser('~'), '.gemini_uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Track which files are in context mode (should persist across prompts)
context_mode_files = set()
last_session_id = None

def clear_upload_directory(except_files=None):
    """Clear upload directory except for specified files."""
    if except_files is None:
        except_files = set()
    
    try:
        for filename in os.listdir(UPLOAD_DIR):
            filepath = os.path.join(UPLOAD_DIR, filename)
            if filename not in except_files and os.path.isfile(filepath):
                try:
                    os.remove(filepath)
                    print(f"Cleaned up: {filename}", flush=True)
                except Exception as e:
                    print(f"Failed to clean {filename}: {e}", flush=True)
    except Exception as e:
        print(f"Error clearing upload directory: {e}", flush=True)

def extract_pdf_to_text(pdf_path, output_path):
    """Extract text from PDF and save to text file."""
    if not HAS_PYPDF2:
        return None
    
    try:
        text_content = []
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            for page_num, page in enumerate(pdf_reader.pages):
                try:
                    text = page.extract_text()
                    if text:
                        text_content.append(f"--- Page {page_num + 1} ---\n{text}")
                except Exception as e:
                    print(f"Error extracting page {page_num}: {e}", flush=True)
        
        if text_content:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write('\n\n'.join(text_content))
            print(f"Extracted PDF to text: {output_path}", flush=True)
            return output_path
    except Exception as e:
        print(f"PDF extraction error: {e}", flush=True)
    
    return None

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


@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Handle file uploads with PDF extraction and context mode support."""
    global last_session_id
    try:
        # Support both multipart form data and JSON with base64
        if request.files and 'file' in request.files:
            # Multipart upload
            file = request.files['file']
            if file.filename == '':
                return jsonify({'error': 'No file selected'}), 400
            
            filename = secure_filename(file.filename)
            file_content = file.read()
            is_context_mode = request.form.get('context_mode', 'false').lower() == 'true'
        elif request.is_json and request.json and 'file' in request.json:
            # JSON with base64 encoded file
            data = request.json
            filename = secure_filename(data.get('filename', 'uploaded_file'))
            file_content = base64.b64decode(data['file'])
            is_context_mode = data.get('context_mode', False)
        else:
            return jsonify({'error': 'No file provided'}), 400
        
        # Get session ID from request (for managing file cleanup across requests)
        session_id = request.headers.get('X-Session-ID', 'default')
        
        # If context mode is False AND it's a new session, clear old uploads
        if not is_context_mode and session_id != last_session_id:
            clear_upload_directory(except_files=context_mode_files)
            last_session_id = session_id
        elif is_context_mode:
            last_session_id = session_id  # Update session even in context mode
        
        # Generate unique filename using hash to avoid collisions
        file_hash = hashlib.md5(file_content).hexdigest()[:8]
        name, ext = os.path.splitext(filename)
        unique_filename = f"{name}_{file_hash}{ext}"
        
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        
        # Save file
        with open(file_path, 'wb') as f:
            f.write(file_content)
        
        print(f"[UPLOAD] File uploaded: {unique_filename} ({len(file_content)} bytes, ext={ext})", flush=True)
        
        # Handle PDF extraction
        extracted_txt_path = None
        if ext.lower() == '.pdf':
            if not HAS_PYPDF2:
                print(f"[UPLOAD] WARNING: PyPDF2 not available, cannot extract PDF", flush=True)
            else:
                print(f"[UPLOAD] Starting PDF extraction for {unique_filename}...", flush=True)
                txt_filename = f"{name}_{file_hash}.txt"
                txt_path = os.path.join(UPLOAD_DIR, txt_filename)
                extraction_start = time.time()
                extracted_txt_path = extract_pdf_to_text(file_path, txt_path)
                extraction_time = time.time() - extraction_start
                if extracted_txt_path:
                    txt_size = os.path.getsize(extracted_txt_path)
                    print(f"[UPLOAD] PDF extraction completed in {extraction_time:.2f}s -> {txt_filename} ({txt_size} bytes)", flush=True)
                else:
                    print(f"[UPLOAD] PDF extraction failed after {extraction_time:.2f}s", flush=True)
        
        # Track file in context mode if requested
        if is_context_mode:
            context_mode_files.add(unique_filename)
            if extracted_txt_path:
                context_mode_files.add(os.path.basename(extracted_txt_path))
        
        return jsonify({
            'success': True,
            'filename': unique_filename,
            'extracted_txt': os.path.basename(extracted_txt_path) if extracted_txt_path else None,
            'size': len(file_content),
            'path': file_path,
            'context_mode': is_context_mode
        })
    
    except Exception as e:
        print(f"Upload error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


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
    
    # Handle file references with system prompt
    system_prompt = ""
    if 'files' in data and data['files']:
        file_list = []
        for file_info in data['files']:
            if isinstance(file_info, str):
                filename = file_info
            elif isinstance(file_info, dict):
                filename = file_info.get('filename', file_info.get('name', ''))
            else:
                continue
            
            if filename:
                # Check if there's an extracted text version
                name, ext = os.path.splitext(filename)
                txt_version = f"{name}.txt"
                txt_path = os.path.join(UPLOAD_DIR, txt_version)
                
                # If extracted text exists, use that
                if os.path.exists(txt_path):
                    file_list.append(f"{txt_version} (extracted from {filename})")
                else:
                    file_list.append(filename)
        
        if file_list:
            system_prompt = f"""SYSTEM CONTEXT:
You are running in NON-INTERACTIVE READ-ONLY mode.

STRICT RESTRICTIONS:
- You can ONLY READ files - never write, edit, modify, or create files
- DO NOT attempt to run shell commands, scripts, or executables
- DO NOT suggest making changes to files
- DO NOT use any file modification operations

AVAILABLE FILES (read-only):
{chr(10).join(f'- {f}' for f in file_list)}

INSTRUCTIONS:
- Read and analyze the files listed above as needed
- Answer the user's question based on the file contents
- Follow the user's prompt explicitly and completely
- If you need information from a file, read it directly
- For PDF files, a text extraction has been provided

"""
    
    # Prepend system prompt if it exists
    if system_prompt:
        prompt = system_prompt + "\nUSER PROMPT:\n" + prompt

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
                print(f"[STREAM] Starting Gemini subprocess...", flush=True)
                process = subprocess.Popen(
                    ['gemini'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, # Merge stderr
                    text=True,
                    bufsize=0, # Unbuffered
                    env=env,
                    cwd=UPLOAD_DIR  # Run in upload dir so files are accessible
                )
                
                print(f"[STREAM] Started gemini REPL (pid={process.pid}, prompt_len={len(prompt)}). Writing prompt...", flush=True)
                
                # Send initial heartbeat to client
                yield "data: [SERVER] Initializing Gemini...\n\n"
                
                # Write prompt and close stdin to signal EOF
                try:
                    process.stdin.write(prompt)
                    process.stdin.close()
                    print(f"[STREAM] Prompt written successfully, waiting for response...", flush=True)
                    yield "data: [SERVER] Prompt sent, waiting for AI response...\n\n"
                except Exception as e:
                     print(f"[STREAM] Error writing to stdin: {e}", flush=True)
                     yield f"data: [ERROR] Failed to send prompt: {e}\n\n"
                     return

                buffer = ""
                last_output_time = time.time()
                chars_received = 0
                
                while True:
                    # Read small chunks for responsive streaming
                    chunk = process.stdout.read(1)
                    
                    # Send heartbeat every 5 seconds if no output
                    if not chunk and time.time() - last_output_time > 5:
                        if process.poll() is None:
                            print(f"[STREAM] Heartbeat: process alive, {chars_received} chars received", flush=True)
                            yield "data: [SERVER] Processing... (still working)\n\n"
                            last_output_time = time.time()
                    
                    if not chunk and process.poll() is not None:
                        # Process finished
                        print(f"[STREAM] Process finished (exit={process.returncode}, chars={chars_received})", flush=True)
                        # Flush remaining buffer
                        if buffer:
                            cleaned_tail = clean_gemini_output(buffer, prompt)
                            if cleaned_tail:
                                yield f"data: {cleaned_tail}\n\n"
                        yield "data: [DONE]\n\n"
                        break
                    
                    if chunk:
                        chars_received += 1
                        last_output_time = time.time()
                        buffer += chunk
                        # Emit complete lines after cleaning
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            cleaned_line = clean_gemini_output(line, prompt)
                            if cleaned_line:
                                yield f"data: {cleaned_line}\n\n"
                
                if process.returncode != 0:
                     print(f"Process exited with code {process.returncode}", flush=True)

            except Exception as e:
                print(f"Exception during generation: {e}", flush=True)
                yield f"\n[Exception: {str(e)}]"

        return app.response_class(generate_output(), mimetype='text/plain')

    else:
        try:
            print(f"[NON-STREAM] Starting subprocess.run (prompt_len={len(prompt)})...", flush=True)
            start_time = time.time()
            
            # Run with timeout to detect hanging
            result = subprocess.run(
                ['gemini', 'chat', prompt],
                capture_output=True,
                text=True,
                check=False,
                env=env,
                cwd=UPLOAD_DIR,  # Run in upload dir so files are accessible
                timeout=300  # 5 minute timeout
            )
            
            elapsed = time.time() - start_time
            print(f"[NON-STREAM] Subprocess finished in {elapsed:.2f}s. Exit code: {result.returncode}", flush=True)
            print(f"[NON-STREAM] Output length: {len(result.stdout)} chars", flush=True)
            
            if result.returncode != 0:
                 print(f"[NON-STREAM] Error output: {result.stderr[:500]}", flush=True)
                 return jsonify({
                     'error': 'Gemini CLI failed',
                     'stderr': result.stderr,
                     'returncode': result.returncode
                 }), 500
                 
            cleaned = clean_gemini_output(result.stdout, prompt)
            print(f"[NON-STREAM] Cleaned output length: {len(cleaned)} chars", flush=True)
            return jsonify({'response': cleaned})
    
        except subprocess.TimeoutExpired:
            print(f"[NON-STREAM] Request timed out after 300 seconds", flush=True)
            return jsonify({'error': 'Request timeout - response took too long. Try with stream=true for long operations.'}), 504
        except Exception as e:
            print(f"[NON-STREAM] Exception: {e}", flush=True)
            return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
