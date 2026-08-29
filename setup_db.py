import sqlite3
from pathlib import Path

# Path where your database file will live
DB_PATH = Path("voice_notes.db")

def init_db():
    # Connecting to a non-existent .db file creates it automatically
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Enable Write-Ahead Logging (WAL) mode for better performance with multiple processes
    cursor.execute("PRAGMA journal_mode=WAL;")

    # 1. Main Queue Table
    cursor.execute("""
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

    # 2. System State Tracking Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Index for fast queue lookups by the LLM worker
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_status ON voice_notes(status);
    """)

    conn.commit()
    conn.close()
    print(f"✅ SQLite database initialized successfully at: {DB_PATH.resolve()}")

if __name__ == "__main__":
    init_db()
