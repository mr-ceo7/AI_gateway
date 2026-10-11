import hashlib
import os
import re
import sqlite3
import time

CACHE_DB_PATH = os.environ.get('PROMPT_CACHE_DB', '/var/www/ai-gateway/response_cache.db')
MAX_CACHE_AGE_DAYS = 30


class PromptCache:
    def __init__(self, db_path=CACHE_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('''CREATE TABLE IF NOT EXISTS response_cache (
                    cache_key TEXT PRIMARY KEY,
                    prompt_norm TEXT,
                    response_text TEXT,
                    created_at REAL,
                    hit_count INTEGER DEFAULT 1
                )''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_cache_created ON response_cache (created_at)')
        except Exception as e:
            print(f'[CACHE] Init error: {e}', flush=True)

    def _normalize(self, prompt: str):
        norm = re.sub(r'\s+', ' ', (prompt or '').strip().lower())
        key = hashlib.sha256(norm.encode('utf-8')).hexdigest()
        return key, norm

    def get(self, prompt: str, max_age_days=MAX_CACHE_AGE_DAYS):
        if not prompt or len(prompt.strip()) < 1:
            return None
        key, _ = self._normalize(prompt)
        cutoff = time.time() - (max_age_days * 86400)
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                row = conn.execute(
                    'SELECT response_text FROM response_cache WHERE cache_key = ? AND created_at > ?',
                    (key, cutoff)
                ).fetchone()
                if row:
                    conn.execute('UPDATE response_cache SET hit_count = hit_count + 1 WHERE cache_key = ?', (key,))
                    return row[0]
        except Exception as e:
            print(f'[CACHE] Get error: {e}', flush=True)
        return None

    def set(self, prompt: str, response_text: str):
        if not prompt or not response_text or len(response_text.strip()) < 1:
            return
        if response_text.startswith('[ERROR]') or 'No response received from AI' in response_text:
            return
        key, norm = self._normalize(prompt)
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.execute('''INSERT INTO response_cache (cache_key, prompt_norm, response_text, created_at, hit_count)
                    VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        response_text = excluded.response_text,
                        created_at = excluded.created_at,
                        hit_count = hit_count + 1
                ''', (key, norm[:500], response_text, time.time()))
        except Exception as e:
            print(f'[CACHE] Set error: {e}', flush=True)

    def stats(self):
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                row = conn.execute('SELECT count(*), coalesce(sum(hit_count), 0) FROM response_cache').fetchone()
                return {'entries': row[0] if row else 0, 'total_hits': row[1] if row else 0}
        except Exception:
            return {'entries': 0, 'total_hits': 0}


prompt_cache = PromptCache()
