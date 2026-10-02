from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
import subprocess
import os
import shutil
import threading
import time
import re
import pty
import fcntl
import base64
import hashlib
import hmac
import json
from werkzeug.utils import secure_filename
from utils.account_pool import AccountPool, is_quota_error
try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

app = Flask(__name__)

# --- Access and safety settings (environment) ---
# GATEWAY_TOKENS: comma-separated secrets; every /api call except the health check must send one
#   (Authorization: Bearer <token>, or X-Gateway-Token). Unset = open, with a warning (old behaviour).
# CORS_ORIGINS: comma-separated websites allowed to call the API from a browser. Unset = none
#   (the gateway's own page and server-to-server callers don't need CORS).
# UPLOAD_TTL_SECONDS: uploads older than this are deleted (default 6 hours).
GATEWAY_TOKENS = {t.strip() for t in os.environ.get('GATEWAY_TOKENS', '').split(',') if t.strip()}
CORS_ORIGINS = [o.strip() for o in os.environ.get('CORS_ORIGINS', '*').split(',') if o.strip()]
UPLOAD_TTL_SECONDS = int(os.environ.get('UPLOAD_TTL_SECONDS', str(6 * 3600)))
# The agent always runs sandboxed and can't expand slash commands from prompt text. What it may do is set in its
# settings (~/.gemini/antigravity-cli/settings.json): no writes, no commands, no access outside the session folder.
AGY_FLAGS = ['--sandbox', '--disable-slash-commands']
if not GATEWAY_TOKENS:
    print("WARNING: GATEWAY_TOKENS is not set: anyone who can reach this gateway can use it.", flush=True)

# Account Rotation Pool for multi-account quota failover
account_pool = AccountPool()

# Enable CORS for API routes and ensure OPTIONS (preflight) requests are handled.
# Allow common headers used by clients (e.g. Content-Type, X-Session-ID).
CORS(app,
     resources={r"/api/*": {"origins": "*" if '*' in CORS_ORIGINS else CORS_ORIGINS}},
     supports_credentials=False,
     allow_headers=["Content-Type", "X-Session-ID", "Authorization", "X-Gateway-Token"],
     expose_headers=["Content-Type", "X-Session-ID"],
     methods=["GET", "POST", "OPTIONS"]
)


@app.before_request
def _handle_cors_preflight():
    # Explicitly respond to CORS preflight requests to ensure nginx/ngrok or other
    # proxies don't drop the `Access-Control-*` headers. This returns a minimal
    # successful response for OPTIONS requests with the appropriate headers.
    if request.method == 'OPTIONS':
        from flask import make_response
        resp = make_response(('', 204))
        origin = request.headers.get('Origin', '')
        if '*' in CORS_ORIGINS or origin in CORS_ORIGINS:
            resp.headers['Access-Control-Allow-Origin'] = origin or '*'
            resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Session-ID, Authorization, X-Gateway-Token'
            resp.headers['Vary'] = 'Origin'
        return resp

    # Allow same-origin browser chat requests (e.g. the built-in web UI)
    is_same_origin = bool(
        request.referrer and request.referrer.startswith(request.host_url)
    )

    # Every API call needs a token, except the health check and same-origin browser chat
    if GATEWAY_TOKENS and request.path.startswith('/api/') and request.path != '/api/auth/status':
        if not is_same_origin:
            auth = request.headers.get('Authorization', '')
            token = auth[7:].strip() if auth.lower().startswith('bearer ') else (
                request.headers.get('X-Gateway-Token', '').strip() or request.args.get('token', '').strip()
            )
            if not any(hmac.compare_digest(token, t) for t in GATEWAY_TOKENS):
                return jsonify({'error': 'Missing or wrong gateway token'}), 401

# Root directory for uploaded files
UPLOAD_DIR = os.path.join(os.path.expanduser('~'), '.gemini_uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Track files in context mode per session: Dict[session_id, set_of_filenames]
context_mode_files = {}

def get_session_upload_dir(session_id=None):
    """Return an isolated directory for the given session ID."""
    safe_session = re.sub(r'[^a-zA-Z0-9_-]', '_', session_id or 'default')[:64]
    session_dir = os.path.join(UPLOAD_DIR, safe_session)
    os.makedirs(session_dir, exist_ok=True)
    return session_dir

def clear_session_upload_directory(session_id, except_files=None):
    """Clear upload directory for a specific session except for specified files."""
    if except_files is None:
        except_files = set()
    session_dir = get_session_upload_dir(session_id)
    try:
        if os.path.isdir(session_dir):
            for filename in os.listdir(session_dir):
                filepath = os.path.join(session_dir, filename)
                if filename not in except_files and os.path.isfile(filepath):
                    try:
                        os.remove(filepath)
                        print(f"[{session_id}] Cleaned up: {filename}", flush=True)
                    except Exception as e:
                        print(f"[{session_id}] Failed to clean {filename}: {e}", flush=True)
    except Exception as e:
        print(f"Error clearing session directory ({session_id}): {e}", flush=True)

def sweep_old_uploads():
    """Delete uploaded files (and emptied session folders) older than UPLOAD_TTL_SECONDS."""
    cutoff = time.time() - UPLOAD_TTL_SECONDS
    try:
        for root, dirs, files in os.walk(UPLOAD_DIR, topdown=False):
            # judged before removing files (removing them refreshes the folder's time)
            old_dir = root != UPLOAD_DIR and os.path.getmtime(root) < cutoff
            for name in files:
                path = os.path.join(root, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                except OSError:
                    pass
            # only folders that are themselves old (never one a request just created)
            if old_dir and not os.listdir(root):
                try:
                    os.rmdir(root)
                except OSError:
                    pass
    except Exception as e:
        print(f"Upload sweep error: {e}", flush=True)


def clear_upload_directory(except_files=None, session_id='default'):
    """Backward compatible wrapper for clearing upload directory."""
    clear_session_upload_directory(session_id, except_files=except_files)

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

def json_answer(stdout):
    """(answer, denied actions) from agy's --output-format json output, or (None, []) if it isn't JSON."""
    for line in reversed((stdout or '').strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        result = data.get('result', data) if isinstance(data, dict) else {}
        if isinstance(result, dict) and ('response' in result or 'status' in result):
            return (result.get('response') or ''), [d.get('action') for d in (result.get('denied_actions') or [])]
    return None, []


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
            
            # Start 'agy' (interactive REPL) to trigger auth flow
            # We connect stdout/stdin to the PTY
            self.auth_process = subprocess.Popen(
                ['agy'],
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



def format_sse(text):
    """Format text as Server-Sent Events (SSE) data lines according to standard.
    Each line of text is prefixed with 'data: ', followed by an empty line delimiter.
    """
    if not text:
        return ""
    if text.startswith("data: ") and text.endswith("\n\n"):
        return text
    lines = text.split('\n')
    return '\n'.join(f"data: {line}" for line in lines) + "\n\n"

def find_generated_images(session_id, conv_id=None, home_dir=None, since_mtime=None):
    """Search for image artifacts generated during a conversation turn.
    Checks:
    1. The conversation's brain folder: ~/.gemini/antigravity-cli/brain/<conv_id>/
    2. Candidate brain folders created/updated recently in account home dirs
    3. The session upload directory: ~/.gemini_uploads/<session_id>/
    Copies any discovered brain images into session_dir for artifact serving.
    Returns list of image filenames available in session_dir.
    """
    session_dir = get_session_upload_dir(session_id)
    IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.gif')
    found_files = []

    search_home_dirs = []
    if home_dir:
        search_home_dirs.append(home_dir)
    primary_acc = account_pool.get_active_account()
    if primary_acc and primary_acc.get('home_dir'):
        if primary_acc['home_dir'] not in search_home_dirs:
            search_home_dirs.append(primary_acc['home_dir'])
    default_home = os.path.expanduser('~')
    if default_home not in search_home_dirs:
        search_home_dirs.append(default_home)

    candidate_conv_dirs = []
    for h in search_home_dirs:
        brain_base = os.path.join(h, '.gemini', 'antigravity-cli', 'brain')
        if not os.path.isdir(brain_base):
            continue

        if conv_id:
            specific = os.path.join(brain_base, conv_id)
            if os.path.isdir(specific) and specific not in candidate_conv_dirs:
                candidate_conv_dirs.append(specific)

        try:
            subdirs = [os.path.join(brain_base, d) for d in os.listdir(brain_base) if os.path.isdir(os.path.join(brain_base, d))]
            subdirs.sort(key=os.path.getmtime, reverse=True)
            for sd in subdirs[:5]:
                if sd not in candidate_conv_dirs:
                    if since_mtime is None or os.path.getmtime(sd) >= (since_mtime - 120):
                        candidate_conv_dirs.append(sd)
        except Exception as e:
            print(f"[IMAGES] Error scanning {brain_base}: {e}", flush=True)

    for c_dir in candidate_conv_dirs:
        try:
            # A. Check transcript.jsonl if present
            transcript_path = os.path.join(c_dir, '.system_generated', 'logs', 'transcript.jsonl')
            if os.path.isfile(transcript_path):
                with open(transcript_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        if '"media"' in line:
                            try:
                                item = json.loads(line)
                                media_list = item.get('media', [])
                                for m in media_list:
                                    uri = m.get('uri', '')
                                    local_path = uri[7:] if uri.startswith('file://') else uri
                                    if local_path and os.path.isfile(local_path) and local_path.lower().endswith(IMAGE_EXTS):
                                        fname = os.path.basename(local_path)
                                        dest = os.path.join(session_dir, fname)
                                        if not os.path.exists(dest):
                                            shutil.copy2(local_path, dest)
                                            print(f"[IMAGES] Copied transcript media {fname} to {session_dir}", flush=True)
                                        if fname not in found_files:
                                            found_files.append(fname)
                            except Exception:
                                pass

            # B. Check image files directly in c_dir root
            for fname in os.listdir(c_dir):
                if fname.lower().endswith(IMAGE_EXTS):
                    fpath = os.path.join(c_dir, fname)
                    if os.path.isfile(fpath):
                        if since_mtime and os.path.getmtime(fpath) < (since_mtime - 15):
                            continue
                        dest = os.path.join(session_dir, fname)
                        if not os.path.exists(dest):
                            shutil.copy2(fpath, dest)
                            print(f"[IMAGES] Copied brain image {fname} to {session_dir}", flush=True)
                        if fname not in found_files:
                            found_files.append(fname)
        except Exception as e:
            print(f"[IMAGES] Error scanning candidate dir {c_dir}: {e}", flush=True)

    # C. Check session_dir itself
    if os.path.isdir(session_dir):
        try:
            for fname in os.listdir(session_dir):
                if fname.lower().endswith(IMAGE_EXTS):
                    fpath = os.path.join(session_dir, fname)
                    if os.path.isfile(fpath):
                        if since_mtime and os.path.getmtime(fpath) < (since_mtime - 15):
                            continue
                        if fname not in found_files:
                            found_files.append(fname)
        except Exception as e:
            print(f"[IMAGES] Error scanning session_dir: {e}", flush=True)

    return found_files


@app.route('/api/artifacts/<session_id>/<filename>', methods=['GET'])
def get_artifact(session_id, filename):
    """Serve an artifact (e.g. generated image) belonging to a session."""
    session_dir = get_session_upload_dir(session_id)
    filename = secure_filename(filename)
    file_path = os.path.join(session_dir, filename)

    if not os.path.isfile(file_path):
        # Try searching across account brain dirs as fallback
        IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.gif')
        if filename.lower().endswith(IMAGE_EXTS):
            search_dirs = [os.path.expanduser('~')]
            for acc in account_pool.list_accounts():
                if acc.get('home_dir') and acc['home_dir'] not in search_dirs:
                    search_dirs.append(acc['home_dir'])
            for hdir in search_dirs:
                brain_base = os.path.join(hdir, '.gemini', 'antigravity-cli', 'brain')
                if os.path.isdir(brain_base):
                    for conv in os.listdir(brain_base):
                        candidate = os.path.join(brain_base, conv, filename)
                        if os.path.isfile(candidate):
                            shutil.copy2(candidate, file_path)
                            return send_from_directory(session_dir, filename)

        return jsonify({'error': 'Artifact not found'}), 404

    return send_from_directory(session_dir, filename)


@app.route('/api/artifacts/<session_id>', methods=['GET'])
def list_artifacts(session_id):
    """List all generated artifacts for a session."""
    session_dir = get_session_upload_dir(session_id)
    IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.gif')
    artifacts = []
    if os.path.isdir(session_dir):
        for f in os.listdir(session_dir):
            if f.lower().endswith(IMAGE_EXTS) and os.path.isfile(os.path.join(session_dir, f)):
                artifacts.append({
                    'filename': f,
                    'url': f'/api/artifacts/{session_id}/{f}',
                    'size': os.path.getsize(os.path.join(session_dir, f))
                })
    return jsonify({'session_id': session_id, 'artifacts': artifacts})


@app.route('/')
def home():
    default_token = next(iter(GATEWAY_TOKENS), '') if GATEWAY_TOKENS else ''
    return render_template('index.html', gateway_token=default_token)


@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Handle file uploads with PDF extraction and context mode support."""
    global last_session_id
    sweep_old_uploads()  # before this request creates its session folder
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
        
        # Get session ID from request (header, form data, or JSON)
        session_id = request.headers.get('X-Session-ID') or (request.form.get('session_id') if request.form else None) or 'default'
        session_dir = get_session_upload_dir(session_id)
        session_context_files = context_mode_files.setdefault(session_id, set())
        
        # Uploads no longer clear the session's other files (files attached together used to delete each other);
        # old files are swept after UPLOAD_TTL_SECONDS instead (see the top of this function)
        
        # Generate unique filename using hash to avoid collisions
        file_hash = hashlib.md5(file_content).hexdigest()[:8]
        name, ext = os.path.splitext(filename)
        unique_filename = f"{name}_{file_hash}{ext}"
        
        file_path = os.path.join(session_dir, unique_filename)
        
        # Save file
        with open(file_path, 'wb') as f:
            f.write(file_content)
        
        print(f"[UPLOAD][{session_id}] File uploaded: {unique_filename} ({len(file_content)} bytes, ext={ext})", flush=True)
        
        # Handle PDF extraction
        extracted_txt_path = None
        if ext.lower() == '.pdf':
            if not HAS_PYPDF2:
                print(f"[UPLOAD][{session_id}] WARNING: PyPDF2 not available, cannot extract PDF", flush=True)
            else:
                print(f"[UPLOAD][{session_id}] Starting PDF extraction for {unique_filename}...", flush=True)
                txt_filename = f"{name}_{file_hash}.txt"
                txt_path = os.path.join(session_dir, txt_filename)
                extraction_start = time.time()
                extracted_txt_path = extract_pdf_to_text(file_path, txt_path)
                extraction_time = time.time() - extraction_start
                if extracted_txt_path:
                    txt_size = os.path.getsize(extracted_txt_path)
                    print(f"[UPLOAD][{session_id}] PDF extraction completed in {extraction_time:.2f}s -> {txt_filename} ({txt_size} bytes)", flush=True)
                else:
                    print(f"[UPLOAD][{session_id}] PDF extraction failed after {extraction_time:.2f}s", flush=True)
        
        # Track file in context mode if requested
        if is_context_mode:
            session_context_files.add(unique_filename)
            if extracted_txt_path:
                session_context_files.add(os.path.basename(extracted_txt_path))
        
        return jsonify({
            'success': True,
            'filename': unique_filename,
            'extracted_txt': os.path.basename(extracted_txt_path) if extracted_txt_path else None,
            'size': len(file_content),
            'context_mode': is_context_mode,
            'session_id': session_id
        })
    
    except Exception as e:
        print(f"Upload error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/accounts', methods=['GET'])
def list_accounts():
    """Return all registered accounts in the rotation pool and their status."""
    accounts = account_pool.list_accounts()
    active = account_pool.get_active_account()
    return jsonify({
        'total': len(accounts),
        'active_account': active.get('id') if active else None,
        'accounts': accounts
    })


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
    
    # Resolve session ID and isolated directory
    session_id = request.headers.get('X-Session-ID') or data.get('session_id') or 'default'
    session_dir = get_session_upload_dir(session_id)

    # Determine active account from pool
    account = account_pool.get_active_account()
    acc_id = account.get('id') if account else 'default'

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
                txt_path = os.path.join(session_dir, txt_version)
                
                # If extracted text exists, use that
                if os.path.exists(txt_path):
                    file_list.append(f"{txt_path} (text extracted from {filename})")
                elif os.path.exists(os.path.join(session_dir, filename)):
                    file_list.append(os.path.join(session_dir, filename))
        
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
- Read files only with your view_file tool, using the paths listed above; never run commands to look at them
- For PDF files, a text extraction has been provided
- For image files (PNG, JPG, JPEG, WEBP, GIF), open them with view_file and look at their contents

"""
    
    # Prepend system prompt if it exists
    if system_prompt:
        prompt = system_prompt + "\nUSER PROMPT:\n" + prompt

    stream = data.get('stream', False)
    # Optional: structured output (a JSON schema the final answer must follow) and reasoning effort
    extra_args = []
    effort = data.get('effort')
    if effort is not None:
        if effort not in ('low', 'medium', 'high', 'max'):
            return jsonify({'error': 'effort must be low, medium, high or max'}), 400
        extra_args += ['--effort', effort]
    json_schema = data.get('json_schema')
    if json_schema is not None:
        if not isinstance(json_schema, dict):
            return jsonify({'error': 'json_schema must be a JSON object'}), 400
        extra_args += ['--json-schema', json.dumps(json_schema)]
        stream = False  # the structured answer arrives whole, in the final result
    # Add debug logging
    print(f"[{session_id}][acc:{acc_id}] Generating with prompt length: {len(prompt)} (stream={stream})", flush=True)
    
    # Set up environment
    env = os.environ.copy()
    env['TERM'] = 'dumb' # Force non-interactive
    env['NO_BROWSER'] = 'true'
    if account and account.get('home_dir'):
        env['HOME'] = account['home_dir']

    if stream:
        def generate_output():
            process = None
            try:
                import json as json_module
                print(f"[STREAM][{session_id}][acc:{acc_id}] Starting agy subprocess...", flush=True)
                process = subprocess.Popen(
                    ['agy', '-p', prompt, '--output-format', 'stream-json', *AGY_FLAGS, *extra_args],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,  # Line buffered
                    env=env,
                    cwd=session_dir  # Run in session dir so files are accessible
                )
                
                print(f"[STREAM][{session_id}][acc:{acc_id}] Started agy (pid={process.pid}). Reading stream...", flush=True)
                
                # Send initial heartbeat to client
                yield "data: [SERVER] Initializing AI...\n\n"
                
                turn_start_time = time.time()
                conv_id = None
                lines_received = 0
                text_sent = False
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    
                    lines_received += 1
                    try:
                        event = json_module.loads(line)
                    except json_module.JSONDecodeError:
                        cleaned = clean_gemini_output(line, prompt)
                        if cleaned:
                            yield format_sse(cleaned)
                        continue
                    
                    event_type = event.get('event', '')
                    if event_type == 'init':
                        conv_id = event.get('conversation_id')
                    elif event_type == 'step_update':
                        step = event.get('step_update', {})
                        if not conv_id:
                            conv_id = step.get('conversation_id')
                        text_delta = step.get('text_delta', '')
                        if text_delta:
                            text_sent = True
                            yield format_sse(text_delta)
                    elif event_type == 'result':
                        result_data = event.get('result', {})
                        if not conv_id:
                            conv_id = result_data.get('conversation_id')
                        status = result_data.get('status', '')
                        if status != 'SUCCESS':
                            error_msg = result_data.get('error', 'Unknown error')
                            if account and is_quota_error(error_msg):
                                print(f"[QUOTA][STREAM] Account '{acc_id}' exhausted quota: {error_msg}", flush=True)
                                account_pool.mark_quota_exhausted(acc_id, error_msg)
                            yield f"data: [ERROR] {error_msg}\n\n"
                        else:
                            if account:
                                account_pool.record_success(acc_id)
                            if not text_sent:
                                # The answer can arrive only in the result, or not at all when the agent tried
                                # something it isn't allowed to do: say so instead of ending silently
                                final = (result_data.get('response') or '').strip()
                                denied = [d.get('action') for d in (result_data.get('denied_actions') or [])]
                                if final:
                                    yield format_sse(final)
                                else:
                                    why = f" (it tried actions that are not allowed: {', '.join(denied)})" if denied else ''
                                    yield f"data: [ERROR] No answer from the AI{why}\n\n"

                            # Detect generated image artifacts and stream markdown
                            images = find_generated_images(
                                session_id=session_id,
                                conv_id=conv_id,
                                home_dir=account.get('home_dir') if account else None,
                                since_mtime=turn_start_time
                            )
                            for img_file in images:
                                title = os.path.splitext(img_file)[0].replace('_', ' ').title()
                                img_markdown = f"\n\n![{title}](/api/artifacts/{session_id}/{img_file})\n\n"
                                yield format_sse(img_markdown)

                        yield "data: [DONE]\n\n"
                        break
                
                process.wait()
                print(f"[STREAM][{session_id}][acc:{acc_id}] Process finished (exit={process.returncode}, lines={lines_received})", flush=True)
                
                if lines_received == 0:
                    stderr_output = process.stderr.read() if process.stderr else ''
                    print(f"[STREAM][{session_id}][acc:{acc_id}] No output received. stderr: {stderr_output[:500]}", flush=True)
                    if account and is_quota_error(stderr_output):
                        print(f"[QUOTA][STREAM] Account '{acc_id}' exhausted quota in stderr.", flush=True)
                        account_pool.mark_quota_exhausted(acc_id, stderr_output)
                    yield f"data: [ERROR] No response received from AI\n\n"
                    yield "data: [DONE]\n\n"
                
            except GeneratorExit:
                print(f"[STREAM][{session_id}][acc:{acc_id}] Client aborted/disconnected.", flush=True)
            except Exception as e:
                print(f"[STREAM][{session_id}][acc:{acc_id}] Exception during generation: {e}", flush=True)
                yield f"data: [ERROR] {str(e)}\n\n"
            finally:
                if process and process.poll() is None:
                    print(f"[STREAM][{session_id}][acc:{acc_id}] Terminating active agy process (pid={process.pid})...", flush=True)
                    try:
                        process.terminate()
                        process.wait(timeout=2)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
                    print(f"[STREAM][{session_id}][acc:{acc_id}] Child process terminated cleanly.", flush=True)

        resp = app.response_class(generate_output(), mimetype='text/event-stream')
        resp.headers['Cache-Control'] = 'no-cache, no-transform'
        resp.headers['X-Accel-Buffering'] = 'no'
        resp.headers['Connection'] = 'keep-alive'
        return resp

    else:
        try:
            print(f"[NON-STREAM][{session_id}][acc:{acc_id}] Starting subprocess.run (prompt_len={len(prompt)})...", flush=True)
            start_time = time.time()
            
            # Run with timeout to detect hanging
            result = subprocess.run(
                ['agy', '-p', prompt, '--output-format', 'json', *AGY_FLAGS, *extra_args],
                capture_output=True,
                text=True,
                check=False,
                env=env,
                cwd=session_dir,  # Run in session dir so files are accessible
                stdin=subprocess.DEVNULL,
                timeout=300  # 5 minute timeout
            )
            
            elapsed = time.time() - start_time
            print(f"[NON-STREAM] Subprocess finished in {elapsed:.2f}s. Exit code: {result.returncode}", flush=True)
            
            if result.returncode != 0:
                print(f"[NON-STREAM] Error output: {result.stderr[:500]}", flush=True)
                # Check for quota exhaustion and perform reactive failover
                if account and is_quota_error(result.stderr):
                    print(f"[QUOTA] Account '{acc_id}' hit quota limit. Failing over to next account...", flush=True)
                    next_acc = account_pool.mark_quota_exhausted(acc_id, result.stderr)
                    if next_acc and next_acc.get('home_dir'):
                        print(f"[QUOTA] Retrying immediately with account: '{next_acc['id']}'...", flush=True)
                        retry_env = env.copy()
                        retry_env['HOME'] = next_acc['home_dir']
                        result = subprocess.run(
                            ['agy', '-p', prompt, '--output-format', 'json', *AGY_FLAGS, *extra_args],
                            capture_output=True,
                            text=True,
                            check=False,
                            env=retry_env,
                            cwd=session_dir,
                            stdin=subprocess.DEVNULL,
                            timeout=300
                        )
                        if result.returncode == 0:
                            account_pool.record_success(next_acc['id'])
                
                if result.returncode != 0:
                    return jsonify({
                        'error': 'AI CLI failed',
                        'stderr': result.stderr,
                        'returncode': result.returncode
                    }), 500
            else:
                if account:
                    account_pool.record_success(acc_id)
                 
            answer, denied = json_answer(result.stdout)
            if answer is None:  # not JSON (older agy): fall back to the text cleaner
                answer = clean_gemini_output(result.stdout, prompt)
            if not answer.strip():
                why = f" (it tried actions that are not allowed: {', '.join(denied)})" if denied else ''
                return jsonify({'error': f'No answer from the AI{why}'}), 502
            cleaned = answer

            # Find conversation ID from json output if present
            conv_id = None
            try:
                for l in reversed((result.stdout or '').strip().splitlines()):
                    d = json.loads(l)
                    if isinstance(d, dict) and 'conversation_id' in d:
                        conv_id = d['conversation_id']
                        break
            except Exception:
                pass

            # Detect generated image artifacts and append markdown
            images = find_generated_images(
                session_id=session_id,
                conv_id=conv_id,
                home_dir=account.get('home_dir') if account else None,
                since_mtime=start_time
            )
            for img_file in images:
                if img_file not in cleaned:
                    title = os.path.splitext(img_file)[0].replace('_', ' ').title()
                    cleaned += f"\n\n![{title}](/api/artifacts/{session_id}/{img_file})\n\n"

            print(f"[NON-STREAM] Cleaned output length: {len(cleaned)} chars (images={len(images)})", flush=True)
            return jsonify({'response': cleaned})
    
        except subprocess.TimeoutExpired:
            print(f"[NON-STREAM] Request timed out after 300 seconds", flush=True)
            return jsonify({'error': 'Request timeout - response took too long. Try with stream=true for long operations.'}), 504
        except Exception as e:
            print(f"[NON-STREAM] Exception: {e}", flush=True)
            return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5055))
    app.run(host='0.0.0.0', port=port)
