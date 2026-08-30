import sqlite3
from contextlib import contextmanager

DB_PATH = "voice_notes.db"

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        
        # Audio processing tracker table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                file_hash TEXT NOT NULL,
                file_mtime REAL NOT NULL,
                status TEXT CHECK(status IN ('PENDING', 'TRANSCRIBED', 'COMPLETED', 'FAILED')) DEFAULT 'PENDING',
                transcription TEXT,
                summary_json TEXT,
                obsidian_path TEXT,
                error_log TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP
            );
        """)
        
        # Vault vector index state tracking table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vault_index (
                file_path TEXT PRIMARY KEY,
                last_mtime REAL NOT NULL,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
