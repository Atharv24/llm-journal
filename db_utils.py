import sqlite3
from contextlib import contextmanager
from typing import Optional
from pathlib import Path
from config import DB_PATH

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

def reset_failed_records() -> int:
    """Resets all records in FAILED status back to PENDING so they can be re-attempted."""
    with get_db() as conn:
        cursor = conn.execute("""
            UPDATE voice_notes 
            SET status = 'PENDING', error_log = NULL 
            WHERE status = 'FAILED'
        """)
        return cursor.rowcount

def reset_all_for_reprocessing(retranscribe: bool = False) -> int:
    """Resets records for reprocessing. If retranscribe=False, keeps existing transcriptions."""
    with get_db() as conn:
        if retranscribe:
            cursor = conn.execute("""
                UPDATE voice_notes 
                SET status = 'PENDING', error_log = NULL, transcription = NULL, summary_json = NULL
            """)
        else:
            cursor = conn.execute("""
                UPDATE voice_notes 
                SET status = 'TRANSCRIBED', error_log = NULL, summary_json = NULL
                WHERE transcription IS NOT NULL AND transcription != ''
            """)
        return cursor.rowcount

def get_records_for_reprocessing(record_id: Optional[int] = None) -> list[sqlite3.Row]:
    """Fetches completed or transcribed records with transcripts available for reprocessing."""
    with get_db() as conn:
        cursor = conn.cursor()
        if record_id is not None:
            cursor.execute("""
                SELECT id, file_path, file_mtime, status, transcription, obsidian_path 
                FROM voice_notes 
                WHERE id = ? AND transcription IS NOT NULL AND transcription != ''
            """, (record_id,))
        else:
            cursor.execute("""
                SELECT id, file_path, file_mtime, status, transcription, obsidian_path 
                FROM voice_notes 
                WHERE transcription IS NOT NULL AND transcription != ''
                ORDER BY id ASC
            """)
        return cursor.fetchall()

def list_records(search_query: Optional[str] = None) -> list[sqlite3.Row]:
    """Returns all records, optionally filtered by keyword in file name, obsidian path, or transcript."""
    with get_db() as conn:
        cursor = conn.cursor()
        if search_query:
            wildcard = f"%{search_query}%"
            cursor.execute("""
                SELECT id, file_path, status, obsidian_path, created_at, processed_at
                FROM voice_notes
                WHERE file_path LIKE ? OR obsidian_path LIKE ? OR transcription LIKE ?
                ORDER BY id ASC
            """, (wildcard, wildcard, wildcard))
        else:
            cursor.execute("""
                SELECT id, file_path, status, obsidian_path, created_at, processed_at
                FROM voice_notes
                ORDER BY id ASC
            """)
        return cursor.fetchall()
