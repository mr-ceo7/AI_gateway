import os
import json
import time
import datetime
import threading
import re
import fcntl
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

DEFAULT_ACCOUNTS_DIR = os.getenv(
    'AGY_ACCOUNTS_DIR',
    os.path.join(os.path.expanduser('~'), '.gemini_accounts')
)

class AccountPool:
    def __init__(self, base_dir: str = DEFAULT_ACCOUNTS_DIR, cooldown_seconds: int = 3600):
        self.base_dir = os.path.abspath(base_dir)
        self.accounts_dir = os.path.join(self.base_dir, 'accounts')
        self.metadata_file = os.path.join(self.base_dir, 'accounts.json')
        self.cooldown_seconds = cooldown_seconds
        self.lock = threading.Lock()
        
        os.makedirs(self.accounts_dir, exist_ok=True)
        self._ensure_metadata_initialized()

    @contextmanager
    def _file_lock(self):
        lock_path = self.metadata_file + ".lock"
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                os.close(lock_fd)
            except Exception:
                pass

    def _ensure_metadata_initialized(self):
        with self.lock:
            with self._file_lock():
                if not os.path.exists(self.metadata_file):
                    initial_data = {
                        "accounts": [],
                        "active_index": 0,
                        "cooldown_seconds": self.cooldown_seconds
                    }
                    with open(self.metadata_file, 'w', encoding='utf-8') as f:
                        json.dump(initial_data, f, indent=2)

    def _load_data(self) -> dict:
        if not os.path.exists(self.metadata_file):
            return {"accounts": [], "active_index": 0, "cooldown_seconds": self.cooldown_seconds}
        try:
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Dynamically resolve home_dir to current environment path
                for acc in data.get('accounts', []):
                    acc['home_dir'] = os.path.join(self.accounts_dir, acc['id'])
                return data
        except Exception as e:
            print(f"[ACCOUNT_POOL] Error reading metadata: {e}", flush=True)
            return {"accounts": [], "active_index": 0, "cooldown_seconds": self.cooldown_seconds}

    def _save_data(self, data: dict):
        try:
            tmp_path = self.metadata_file + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self.metadata_file)
        except Exception as e:
            print(f"[ACCOUNT_POOL] Error saving metadata: {e}", flush=True)

    def list_accounts(self) -> List[dict]:
        with self.lock:
            with self._file_lock():
                data = self._load_data()
                now = time.time()
                # Refresh cooldown states
                for acc in data.get('accounts', []):
                    cooldown = acc.get('cooldown_until')
                    if cooldown and now >= cooldown:
                        acc['cooldown_until'] = None
                        acc['status'] = 'ready'
                return data.get('accounts', [])

    def get_active_account(self) -> Optional[dict]:
        """
        Returns the current active account dict with its isolated home_dir.
        Checks for quota cooldown and automatically rolls to the next ready account if needed.
        """
        with self.lock:
            with self._file_lock():
                data = self._load_data()
                accounts = data.get('accounts', [])
                if not accounts:
                    return None

                now = time.time()
                total = len(accounts)
                start_index = data.get('active_index', 0) % total

                # Check if current account is ready
                for i in range(total):
                    idx = (start_index + i) % total
                    acc = accounts[idx]
                    cooldown = acc.get('cooldown_until')

                    # Check if cooldown has expired
                    if cooldown and now >= cooldown:
                        acc['cooldown_until'] = None
                        acc['status'] = 'ready'

                    if not acc.get('cooldown_until') and acc.get('status') != 'disabled':
                        if idx != start_index:
                            data['active_index'] = idx
                            self._save_data(data)
                            print(f"[ACCOUNT_POOL] Switched active account to: '{acc['id']}'", flush=True)
                        return acc

                # All accounts are on cooldown; return the one whose cooldown expires earliest
                sorted_by_cooldown = sorted(
                    accounts,
                    key=lambda a: a.get('cooldown_until') or float('inf')
                )
                earliest = sorted_by_cooldown[0]
                print(f"[ACCOUNT_POOL] WARNING: All accounts are in quota cooldown. Using earliest: '{earliest['id']}'", flush=True)
                return earliest

    def mark_quota_exhausted(self, account_id: str, error_msg: str = "") -> Optional[dict]:
        """
        Marks account_id as quota exhausted and rolls to the next available account.
        Returns the new active account.
        """
        with self.lock:
            with self._file_lock():
                data = self._load_data()
                accounts = data.get('accounts', [])
                if not accounts:
                    return None

                now = time.time()
                cooldown_period = data.get('cooldown_seconds', self.cooldown_seconds)
                cooldown_until = now + cooldown_period

                target_idx = None
                for idx, acc in enumerate(accounts):
                    if acc.get('id') == account_id:
                        acc['status'] = 'quota_exhausted'
                        acc['cooldown_until'] = cooldown_until
                        acc['last_exhausted_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        acc['fail_count'] = acc.get('fail_count', 0) + 1
                        target_idx = idx
                        print(f"[ACCOUNT_POOL] Account '{account_id}' marked quota exhausted for {cooldown_period // 60}m. Error: {error_msg[:120]}", flush=True)
                        break

                # Roll to next ready account
                total = len(accounts)
                next_acc = None
                if target_idx is not None:
                    for i in range(1, total + 1):
                        cand_idx = (target_idx + i) % total
                        cand = accounts[cand_idx]
                        cd = cand.get('cooldown_until')
                        if cd and now >= cd:
                            cand['cooldown_until'] = None
                            cand['status'] = 'ready'
                        if not cand.get('cooldown_until') and cand.get('status') != 'disabled':
                            data['active_index'] = cand_idx
                            next_acc = cand
                            print(f"[ACCOUNT_POOL] Failover successful! New active account: '{cand['id']}'", flush=True)
                            break

                self._save_data(data)
                return next_acc

    def record_success(self, account_id: str):
        with self.lock:
            with self._file_lock():
                data = self._load_data()
                for acc in data.get('accounts', []):
                    if acc.get('id') == account_id:
                        acc['success_count'] = acc.get('success_count', 0) + 1
                        acc['last_used_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        break
                self._save_data(data)

    def add_account_from_dir(self, account_id: str, source_gemini_dir: str, email: str = "") -> dict:
        """
        Creates an isolated home for account_id and copies tokens from source_gemini_dir.
        """
        clean_id = re.sub(r'[^a-zA-Z0-9_-]', '_', account_id).strip('_')
        account_home = os.path.join(self.accounts_dir, clean_id)
        target_gemini = os.path.join(account_home, '.gemini', 'antigravity-cli')
        os.makedirs(target_gemini, exist_ok=True)

        # Copy essential token files
        for fname in ['antigravity-oauth-token', 'settings.json', 'installation_id', 'antigravity_state.pbtxt']:
            src = os.path.join(source_gemini_dir, fname)
            if os.path.isfile(src):
                dst = os.path.join(target_gemini, fname)
                with open(src, 'rb') as f_in, open(dst, 'wb') as f_out:
                    f_out.write(f_in.read())

        # Extract email from token or google_accounts if not provided
        if not email:
            tok_path = os.path.join(target_gemini, 'antigravity-oauth-token')
            if os.path.isfile(tok_path):
                try:
                    with open(tok_path, 'r') as f:
                        tok_data = json.load(f)
                        id_tok = tok_data.get('id_token')
                        if id_tok and id_tok.count('.') == 2:
                            import base64
                            payload = id_tok.split('.')[1]
                            payload += '=' * (-len(payload) % 4)
                            claims = json.loads(base64.b64decode(payload).decode('utf-8', errors='ignore'))
                            email = claims.get('email', '')
                except Exception:
                    pass

        with self.lock:
            with self._file_lock():
                data = self._load_data()
                accounts = data.get('accounts', [])

                # Update if exists, else append
                existing = next((a for a in accounts if a['id'] == clean_id), None)
                record = {
                    "id": clean_id,
                    "email": email or clean_id,
                    "home_dir": account_home,
                    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "status": "ready",
                    "cooldown_until": None,
                    "success_count": 0,
                    "fail_count": 0
                }

                if existing:
                    existing.update(record)
                else:
                    accounts.append(record)

                data['accounts'] = accounts
                self._save_data(data)
                return record

    def remove_account(self, account_id: str) -> bool:
        with self.lock:
            with self._file_lock():
                data = self._load_data()
                accounts = data.get('accounts', [])
                new_accounts = [a for a in accounts if a['id'] != account_id]
                if len(new_accounts) == len(accounts):
                    return False
                data['accounts'] = new_accounts
                if data.get('active_index', 0) >= len(new_accounts):
                    data['active_index'] = 0
                self._save_data(data)
                return True

def is_quota_error(text: str) -> bool:
    """Detects whether error text indicates quota exhaustion or rate limiting."""
    if not text:
        return False
    patterns = [
        r'429',
        r'RESOURCE_EXHAUSTED',
        r'quota.*exceeded',
        r'rate.*limit',
        r'exhausted.*quota',
        r'too many requests',
        r'resource has been exhausted',
        r'exceeded your current quota'
    ]
    low = text.lower()
    return any(re.search(p, low) for p in patterns)

def ensure_account_symlinks(account_home: str):
    """
    Guarantees global configs, SSH keys, skills, plugins, and unified conversations
    are accessible inside the isolated account home without token contamination.
    """
    real_home = os.path.expanduser("~")
    if not account_home or account_home == real_home:
        return

    # 1. Global tools and configurations
    for item in [".gitconfig", ".ssh"]:
        src = os.path.join(real_home, item)
        dst = os.path.join(account_home, item)
        if os.path.exists(src) and not os.path.exists(dst) and not os.path.islink(dst):
            try:
                os.symlink(src, dst)
            except Exception:
                pass

    # 2. Shared plugins, skills, and prompts
    gem_src = os.path.join(real_home, ".gemini", "config")
    gem_dst = os.path.join(account_home, ".gemini", "config")
    if os.path.exists(gem_src) and not os.path.exists(gem_dst) and not os.path.islink(gem_dst):
        try:
            os.symlink(gem_src, gem_dst)
        except Exception:
            pass

    # 3. Unified conversation history, transcripts, and command line cache
    central_cli = os.path.join(real_home, ".gemini", "antigravity-cli")
    acc_cli = os.path.join(account_home, ".gemini", "antigravity-cli")
    os.makedirs(acc_cli, exist_ok=True)

    shared_items = ["brain", "conversations", "conversation_summaries.db", "history.jsonl", "cache"]
    for item in shared_items:
        src = os.path.join(central_cli, item)
        dst = os.path.join(acc_cli, item)

        if not os.path.exists(src):
            if "." in item:
                open(src, "w").close()
            else:
                os.makedirs(src, exist_ok=True)

        if not os.path.exists(dst) and not os.path.islink(dst):
            try:
                os.symlink(src, dst)
            except Exception:
                pass
