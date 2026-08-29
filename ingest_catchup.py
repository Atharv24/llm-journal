import os
import sys
import hashlib
import sqlite3
from pathlib import Path

# Update this path to match your actual voice notes directory
WATCH_DIR = Path(r"D:\iCloudDrive\voice notes")
DB_PATH = Path("voice_notes.db")

AUDIO_EXTENSIONS = ('.m4a', '.mp3', '.wav', '.ogg', '.flac')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode and busy timeout to handle concurrent accesses gracefully
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def compute_hash(file_path):
    """Computes SHA-256 hash of the file to track content changes."""
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def normalize_and_rename(file_path: Path) -> Path:
    """Removes unicode space characters (\u202f, \u00a0) from filename on disk."""
    original_name = file_path.name
    
    # Replace narrow non-breaking space and non-breaking space with standard space
    clean_name = original_name.replace('\u202f', ' ').replace('\u00a0', ' ')
    
    if clean_name != original_name:
        clean_path = file_path.parent / clean_name
        
        # Avoid overwriting if destination already exists
        if clean_path.exists():
            print(f"  [!] Target name exists, skipping rename: {clean_name}")
            return file_path
            
        try:
            file_path.rename(clean_path)
            print(f"  [✏️ Renamed]: '{original_name}' -> '{clean_name}'")
            return clean_path
        except Exception as e:
            print(f"  [❌ Rename Failed]: {e}")
            return file_path
            
    return file_path

def register_file(conn, file_path):
    """Inserts or updates a single file in the database."""
    try:
        mtime = os.path.getmtime(file_path)
        f_hash = compute_hash(file_path)
        
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO voice_notes (file_path, file_hash, file_mtime, status)
            VALUES (?, ?, ?, 'PENDING')
            ON CONFLICT(file_path) DO UPDATE SET
                file_hash = excluded.file_hash,
                file_mtime = excluded.file_mtime,
                status = CASE 
                    WHEN voice_notes.file_hash != excluded.file_hash THEN 'PENDING' 
                    ELSE voice_notes.status 
                END
        """, (str(file_path), f_hash, mtime))
        
        return cursor.rowcount > 0
    except Exception as e:
        print(f"❌ Error scanning file '{file_path}': {e}")
        return False

def run_catchup():
    if not WATCH_DIR.exists():
        print(f"❌ Directory does not exist: {WATCH_DIR.resolve()}")
        sys.exit(1)
        
    if not DB_PATH.exists():
        print(f"❌ Database not found at '{DB_PATH.resolve()}'. Run setup_db.py first.")
        sys.exit(1)

    print(f"🔍 Scanning '{WATCH_DIR}' for catch-up files...")
    
    total_found = 0
    new_or_updated = 0

    with get_db() as conn:
        for root, _, files in os.walk(WATCH_DIR):
            for file in files:
                if file.lower().endswith(AUDIO_EXTENSIONS):
                    total_found += 1
                    raw_path = Path(root) / file
                    
                    # 1. Clean filename on disk if needed
                    final_path = normalize_and_rename(raw_path)
                    
                    # 2. Register normalized path in database
                    if register_file(conn, final_path):
                        new_or_updated += 1
                        print(f"  [+] Ingested: {final_path.name}")

    print("\n" + "=" * 50)
    print(f"✅ Ingestion complete.")
    print(f"   • Total audio files scanned: {total_found}")
    print(f"   • New/Modified files queued: {new_or_updated}")
    print("=" * 50)

if __name__ == "__main__":
    run_catchup()
