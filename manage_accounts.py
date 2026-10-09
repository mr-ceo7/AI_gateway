#!/usr/bin/env python3
"""
CLI tool to manage multi-account authentication and quota rotation pool for agy.

Usage:
  python manage_accounts.py list
  python manage_accounts.py import-current <account_name>
  python manage_accounts.py import-keyring <account_name>
  python manage_accounts.py add <account_name>
  python manage_accounts.py test [account_name]
  python manage_accounts.py remove <account_name>
  python manage_accounts.py sync-to-prod
"""

import sys
import os
import subprocess
import shutil
import time
import json
import shlex
from utils.account_pool import AccountPool

def _load_env_file():
    """Load environment variables from local .env file if it exists."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.isfile(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env_file()

PROD_HOST = os.getenv("PROD_HOST", "")
PROD_USER = os.getenv("PROD_USER", "root")
PROD_PASS = os.getenv("PROD_PASS", "")
# token for the production gateway's API (defaults to the first local GATEWAY_TOKENS entry)
PROD_GATEWAY_TOKEN = os.getenv("PROD_GATEWAY_TOKEN") or next(
    (t.strip() for t in os.getenv("GATEWAY_TOKENS", "").split(",") if t.strip()), "")

def get_remote_commands():
    if not PROD_HOST:
        print("\nError: PROD_HOST is not configured.")
        print("Please define PROD_HOST, PROD_USER, and PROD_PASS in your .env file or environment.")
        print("See .env.example for a template.\n")
        sys.exit(1)

    if PROD_PASS:
        ssh_cmd = f"sshpass -p {shlex.quote(PROD_PASS)} ssh -o StrictHostKeyChecking=no {PROD_USER}@{PROD_HOST}"
        scp_cmd = f"sshpass -p {shlex.quote(PROD_PASS)} scp -o StrictHostKeyChecking=no"
    else:
        ssh_cmd = f"ssh -o StrictHostKeyChecking=no {PROD_USER}@{PROD_HOST}"
        scp_cmd = "scp -o StrictHostKeyChecking=no"
    return ssh_cmd, scp_cmd

def get_pool():
    return AccountPool()

def cmd_list(args):
    pool = get_pool()
    accounts = pool.list_accounts()
    active = pool.get_active_account()
    active_id = active.get('id') if active else None

    print("\n" + "=" * 70)
    print("  AGY ACCOUNT ROTATION POOL")
    print("=" * 70)

    if not accounts:
        print("  No accounts registered yet.")
        print("  Run: python manage_accounts.py import-current <name>")
        print("   or: python manage_accounts.py add <name>")
        print("=" * 70 + "\n")
        return

    now = time.time()
    for idx, acc in enumerate(accounts, 1):
        is_active = (acc['id'] == active_id)
        marker = "★ [ACTIVE]" if is_active else "  [READY] "
        
        status_str = acc.get('status', 'ready').upper()
        cooldown = acc.get('cooldown_until')
        if cooldown and now < cooldown:
            mins_left = int((cooldown - now) // 60)
            status_str = f"COOLDOWN ({mins_left}m remaining)"
            marker = "⏳ [COOLDOWN]"

        print(f"\n{marker} #{idx}: {acc['id']}")
        print(f"   Email:    {acc.get('email', 'N/A')}")
        print(f"   Status:   {status_str}")
        print(f"   Requests: {acc.get('success_count', 0)} succeeded, {acc.get('fail_count', 0)} failed")
        print(f"   Home Dir: {acc['home_dir']}")

    print("\n" + "=" * 70 + "\n")

def cmd_import_current(args):
    if not args:
        print("Error: Account name required.\nExample: python manage_accounts.py import-current main_account")
        sys.exit(1)

    acc_name = args[0]
    local_gemini = os.path.expanduser("~/.gemini/antigravity-cli")
    token_file = os.path.join(local_gemini, "antigravity-oauth-token")

    if not os.path.isfile(token_file):
        print(f"Error: No token found at {token_file}. Please run 'agy' first to log in.")
        sys.exit(1)

    pool = get_pool()
    record = pool.add_account_from_dir(acc_name, local_gemini)
    print(f"✓ Imported current local account into pool as '{record['id']}'")
    print(f"  Email: {record.get('email')}")
    print(f"  Isolated Home: {record['home_dir']}")

def cmd_import_keyring(args):
    acc_name = args[0] if args else "kassimmusa"
    pool = get_pool()
    try:
        out = subprocess.check_output(
            ['secret-tool', 'lookup', 'service', 'gemini', 'username', 'antigravity'],
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
        data = json.loads(out)
        
        temp_home = f"/tmp/agy_keyring_{acc_name}_{int(time.time())}"
        temp_gem = os.path.join(temp_home, ".gemini", "antigravity-cli")
        os.makedirs(temp_gem, exist_ok=True)
        with open(os.path.join(temp_gem, "antigravity-oauth-token"), "w") as f:
            json.dump(data, f)
            
        record = pool.add_account_from_dir(acc_name, temp_gem)
        shutil.rmtree(temp_home, ignore_errors=True)
        print(f"✓ Imported keyring account into pool as '{record['id']}'")
        print(f"  Email: {record.get('email')}")
        print(f"  Isolated Home: {record['home_dir']}")
    except Exception as e:
        print(f"Failed to import from keyring: {e}")

def cmd_add(args):
    if not args:
        print("Error: Account name required.\nExample: python manage_accounts.py add backup_account")
        sys.exit(1)

    acc_name = args[0]
    pool = get_pool()
    
    temp_home = f"/tmp/agy_login_{acc_name}_{int(time.time())}"
    temp_gemini = os.path.join(temp_home, ".gemini", "antigravity-cli")
    os.makedirs(temp_gemini, exist_ok=True)

    print(f"\n[LOGIN] Launching isolated agy authentication for '{acc_name}'...")
    print("A Google OAuth URL will appear below.")
    print("1. Click the URL (or Ctrl+Click in your terminal) to open the browser login.")
    print("2. Choose or log into your Google Account.")
    print("3. Copy the authorization code and paste it back here.\n")

    env = os.environ.copy()
    env['HOME'] = temp_home
    # Disable silent desktop keyring authentication by simulating headless environment
    env['SSH_CONNECTION'] = '127.0.0.1 12345 127.0.0.1 22'
    env['SSH_CLIENT'] = '127.0.0.1 12345 22'

    try:
        # Launch agy in isolated environment to authenticate
        subprocess.run(['agy', '-p', 'Hello', '--dangerously-skip-permissions'], env=env, check=False)
    except Exception as e:
        print(f"Error launching agy: {e}")
        shutil.rmtree(temp_home, ignore_errors=True)
        sys.exit(1)

    # Check if token was generated
    token_path = os.path.join(temp_gemini, "antigravity-oauth-token")
    if not os.path.isfile(token_path):
        print("\n[FAILED] Login was not completed. No oauth token found.")
        shutil.rmtree(temp_home, ignore_errors=True)
        sys.exit(1)

    record = pool.add_account_from_dir(acc_name, temp_gemini)
    shutil.rmtree(temp_home, ignore_errors=True)

    print(f"\n✓ Successfully registered account '{record['id']}' into pool!")
    print(f"  Email: {record.get('email')}")
    print(f"  Isolated Home: {record['home_dir']}")

def cmd_test(args):
    pool = get_pool()
    target_name = args[0] if args else None
    accounts = pool.list_accounts()

    if target_name:
        accounts = [a for a in accounts if a['id'] == target_name]
        if not accounts:
            print(f"Account '{target_name}' not found in pool.")
            sys.exit(1)

    if not accounts:
        print("No accounts to test.")
        sys.exit(1)

    print("\n--- Testing Accounts in Pool ---")
    for acc in accounts:
        acc_id = acc['id']
        acc_home = acc['home_dir']
        print(f"\nTesting '{acc_id}' (Email: {acc.get('email', 'N/A')})...")

        env = os.environ.copy()
        env['HOME'] = acc_home
        env['TERM'] = 'dumb'

        start = time.time()
        res = subprocess.run(
            ['agy', '-p', 'Respond with PONG'],
            env=env,
            capture_output=True,
            text=True,
            timeout=30
        )
        elapsed = time.time() - start

        if res.returncode == 0:
            resp_snippet = res.stdout.strip().replace('\n', ' ')[:60]
            print(f"  ✓ SUCCESS ({elapsed:.1f}s): {resp_snippet}")
            pool.record_success(acc_id)
        else:
            err_snippet = res.stderr.strip()[:100]
            print(f"  ✗ FAILED ({elapsed:.1f}s): Exit {res.returncode}, {err_snippet}")

    print("\nTest completed.")

def cmd_remove(args):
    if not args:
        print("Error: Account name required.")
        sys.exit(1)
    acc_name = args[0]
    pool = get_pool()
    if pool.remove_account(acc_name):
        print(f"✓ Removed account '{acc_name}' from pool.")
    else:
        print(f"Account '{acc_name}' not found.")

def cmd_sync_to_prod(args):
    prod_ssh, prod_scp = get_remote_commands()
    remote_target = f"{PROD_USER}@{PROD_HOST}"
    print(f"\n[SYNC] Synchronizing code and account pool to production server ({remote_target})...")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    local_accounts_dir = os.path.expanduser("~/.gemini_accounts")
    if not os.path.isdir(local_accounts_dir):
        print(f"Error: {local_accounts_dir} does not exist.")
        sys.exit(1)

    # 1. Sync updated gateway code files
    print("Uploading code updates (app.py, utils/account_pool.py, manage_accounts.py, templates/index.html)...")
    app_py = os.path.join(base_dir, "app.py")
    acc_pool_py = os.path.join(base_dir, "utils", "account_pool.py")
    manage_py = os.path.join(base_dir, "manage_accounts.py")
    index_html = os.path.join(base_dir, "templates", "index.html")

    subprocess.run(f"{prod_scp} {app_py} {remote_target}:/var/www/ai-gateway/app.py", shell=True, check=True)
    subprocess.run(f"{prod_scp} {acc_pool_py} {remote_target}:/var/www/ai-gateway/utils/account_pool.py", shell=True, check=True)
    subprocess.run(f"{prod_scp} {manage_py} {remote_target}:/var/www/ai-gateway/manage_accounts.py", shell=True, check=True)
    subprocess.run(f"{prod_scp} {index_html} {remote_target}:/var/www/ai-gateway/templates/index.html", shell=True, check=True)

    # 2. Tar the accounts directory
    tar_path = "/tmp/accounts_pool.tar.gz"
    subprocess.run(
        ["tar", "-czf", tar_path, "-C", os.path.expanduser("~"), ".gemini_accounts"],
        check=True
    )

    # 3. Upload accounts pool archive to remote
    print("Uploading accounts pool archive...")
    subprocess.run(
        f"{prod_scp} {tar_path} {remote_target}:/tmp/",
        shell=True,
        check=True
    )

    # 4. Extract on remote, adjust permissions, restart service
    print("Applying remote configuration and restarting ai-gateway.service...")
    remote_cmd = (
        "tar -xzf /tmp/accounts_pool.tar.gz -C /root/ && "
        "chown -R root:root /root/.gemini_accounts && "
        "chmod -R 700 /root/.gemini_accounts && "
        "chown -R root:www-data /var/www/ai-gateway && "
        "rm -f /tmp/accounts_pool.tar.gz && "
        "systemctl restart ai-gateway.service && "
        "sleep 2 && "
        f"curl -s -H 'Authorization: Bearer {PROD_GATEWAY_TOKEN}' http://127.0.0.1:5055/api/accounts"
    )
    res = subprocess.run(f'{prod_ssh} "{remote_cmd}"', shell=True, capture_output=True, text=True)
    os.remove(tar_path)
    
    if res.returncode == 0:
        print("\n✓ Production sync succeeded!")
        print("Production /api/accounts status:")
        print(f"  {res.stdout.strip()}")
    else:
        print(f"\n✗ Sync failed: {res.stderr.strip()}")
        sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()
    args = sys.argv[2:]

    commands = {
        'list': cmd_list,
        'import-current': cmd_import_current,
        'import-keyring': cmd_import_keyring,
        'add': cmd_add,
        'test': cmd_test,
        'remove': cmd_remove,
        'sync-to-prod': cmd_sync_to_prod
    }

    handler = commands.get(cmd)
    if not handler:
        print(f"Unknown command: '{cmd}'\n")
        print(__doc__)
        sys.exit(1)

    handler(args)

if __name__ == "__main__":
    main()
