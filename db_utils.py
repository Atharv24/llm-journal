import sqlite3
from pathlib import Path

DB_PATH = Path("voice_notes.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                file_hash TEXT NOT NULL,
                file_mtime REAL NOT NULL,
                status TEXT CHECK(status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')) DEFAULT 'PENDING',
                transcription TEXT,
                error_log TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON voice_notes(status);")

if __name__ == "__main__":
    init_db()
    print("✅ Database initialized with WAL mode.")
