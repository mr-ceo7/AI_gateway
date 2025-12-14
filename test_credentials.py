#!/usr/bin/env python3
"""
Test script to verify credential detection logic on the local machine.
Does NOT check for GEMINI_API_KEY env var.
"""

import os
import json
import sys
from pathlib import Path


def check_credentials():
    """
    Mimics the credential detection logic from GeminiAuthenticator.check_auth_status(),
    but excludes GEMINI_API_KEY check.
    Returns (is_authenticated, credentials_path, details)
    """
    home = os.path.expanduser('~')
    
    # Primary indicator: Check for oauth_creds.json (actual Gemini CLI creds file)
    oauth_creds_path = os.path.join(home, '.gemini', 'oauth_creds.json')
    settings_path = os.path.join(home, '.gemini', 'settings.json')
    accounts_path = os.path.join(home, '.gemini', 'google_accounts.json')
    
    details = {
        'home': home,
        'gemini_dir': os.path.join(home, '.gemini'),
        'checked_files': {
            'oauth_creds.json': oauth_creds_path,
            'settings.json': settings_path,
            'google_accounts.json': accounts_path,
        },
        'found_credentials': None,
        'found_path': None,
    }
    
    # Check for oauth_creds.json with access_token or refresh_token
    try:
        if os.path.isfile(oauth_creds_path):
            print(f"✓ Found file: {oauth_creds_path}")
            with open(oauth_creds_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            if not content:
                print(f"  → File is empty, skipping")
            else:
                try:
                    data = json.loads(content)
                    print(f"  → Valid JSON loaded")
                    
                    # Check for OAuth tokens
                    keys = {'access_token', 'refresh_token', 'id_token', 'token_type'}
                    found_keys = [k for k in keys if k in data]
                    
                    if found_keys:
                        print(f"  → Found OAuth token keys: {found_keys}")
                        details['found_credentials'] = found_keys
                        details['found_path'] = oauth_creds_path
                        return True, oauth_creds_path, details
                    else:
                        print(f"  → No token keys found. File keys: {list(data.keys())}")
                except json.JSONDecodeError as e:
                    print(f"  → Invalid JSON: {e}")
        else:
            print(f"✗ Not found: {oauth_creds_path}")
    except (PermissionError, OSError) as e:
        print(f"✗ Error reading {oauth_creds_path}: {e}")
    
    # Fallback: Check for settings.json AND google_accounts.json as indicators
    try:
        settings_exists = os.path.isfile(settings_path)
        accounts_exists = os.path.isfile(accounts_path)
        
        if settings_exists:
            print(f"✓ Found file: {settings_path}")
        else:
            print(f"✗ Not found: {settings_path}")
            
        if accounts_exists:
            print(f"✓ Found file: {accounts_path}")
            # Check if it has an active account
            with open(accounts_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            if content:
                data = json.loads(content)
                if 'active' in data and data['active']:
                    print(f"  → Active account: {data['active']}")
                    # If both files exist and accounts has active account, likely authenticated
                    if settings_exists:
                        print(f"  → Settings and active account found, likely authenticated")
                        details['found_credentials'] = ['settings.json', 'active_account']
                        details['found_path'] = accounts_path
                        return True, accounts_path, details
        else:
            print(f"✗ Not found: {accounts_path}")
    
    except (PermissionError, OSError, json.JSONDecodeError) as e:
        print(f"  → Error checking fallback files: {e}")
    
    
    return False, None, details


def main():
    print("=" * 70)
    print("Testing Credential Detection (excluding GEMINI_API_KEY)")
    print("=" * 70)
    print()
    
    is_authed, cred_path, details = check_credentials()
    
    print()
    print("=" * 70)
    print("Results:")
    print("=" * 70)
    print(f"Authenticated: {is_authed}")
    if cred_path:
        print(f"Credentials Path: {cred_path}")
        print(f"Credential Keys Found: {details['found_credentials']}")
    else:
        print("Credentials Path: None")
    
    print()
    print("Files Checked:")
    for name, path in details['checked_files'].items():
        exists = "✓" if os.path.isfile(path) else "✗"
        print(f"  {exists} {name}: {path}")
    
    print()
    if is_authed:
        print("✓ SUCCESS: Credentials found on this machine!")
        return 0
    else:
        print("✗ NO CREDENTIALS: No Gemini CLI credentials found.")
        print()
        print("Gemini CLI stores credentials at ~/.gemini/oauth_creds.json")
        print("This file is created automatically when you authenticate with 'gemini login'")
        home = os.path.expanduser('~')
        gemini_dir = os.path.join(home, '.gemini')
        print()
        print("To authenticate, run:")
        print(f"  gemini chat")
        print("  # Follow the interactive login prompt")
        print()
        return 1


if __name__ == '__main__':
    sys.exit(main())
