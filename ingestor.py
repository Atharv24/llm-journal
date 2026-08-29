import os
import re
import sys
import time
import hashlib
from pathlib import Path
from db_utils import get_db, init_db

WATCH_DIR = Path(r"D:\iCloudDrive\voice notes")
AUDIO_EXTENSIONS = ('.m4a', '.wav', '.mp3', '.ogg', '.flac')

def wait_for_file_ready(filepath, timeout=15):
    """Wait until iCloud finishes downloading and the file is unlocked."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with open(filepath, "rb") as f:
                f.read(100)
            return True
        except (PermissionError, IOError):
            time.sleep(1)
    return False

def compute_hash(file_path):
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def normalize_and_rename(file_path: Path) -> Path:
    """Strip non-ASCII characters (like U+202F, non-breaking spaces) from the filename."""
    original_name = file_path.name
    # Replace narrow spaces/non-breaking spaces with standard space first
    clean_name = original_name.replace('\u202f', ' ').replace('\u00a0', ' ')
    # Strip any remaining non-ASCII characters
    clean_name = re.sub(r'[^\x00-\x7F]+', '', clean_name).strip()

    if clean_name != original_name and clean_name:
        clean_path = file_path.parent / clean_name
        if clean_path.exists():
            print(f"  [!] Target name already exists, skipping rename: {clean_name}")
            return file_path
        try:
            file_path.rename(clean_path)
            print(f"  [✏️ Renamed]: '{original_name}' -> '{clean_name}'")
            return clean_path
        except OSError as e:
            print(f"  [❌ Rename Error]: {e}")
            return file_path
            
    return file_path

def register_file(conn, file_path: Path):
    if not wait_for_file_ready(file_path):
        print(f"  [⏳ Skipped]: File locked/downloading via iCloud: {file_path.name}")
        return False

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
        print(f"❌ Error registering '{file_path.name}': {e}")
        return False

def run_ingestion():
    init_db()
    
    if not WATCH_DIR.exists():
        print(f"❌ Directory does not exist: {WATCH_DIR}")
        sys.exit(1)

    print(f"🔍 Scanning '{WATCH_DIR}' for catch-up files...")
    total_found = 0
    queued = 0

    with get_db() as conn:
        for root, _, files in os.walk(WATCH_DIR):
            for file in files:
                if file.lower().endswith(AUDIO_EXTENSIONS) and not file.endswith(".icloud") and ".tmp" not in file:
                    total_found += 1
                    raw_path = Path(root) / file
                    
                    # Clean filename on disk if non-ASCII characters exist
                    clean_path = normalize_and_rename(raw_path)
                    
                    # Register into SQLite queue
                    if register_file(conn, clean_path):
                        queued += 1
                        print(f"  [+] Ingested: {clean_path.name}")

    print("\n" + "=" * 50)
    print(f"✅ Ingestion scan complete.")
    print(f"   • Total valid audio files scanned: {total_found}")
    print(f"   • Files newly queued or updated: {queued}")
    print("=" * 50)

if __name__ == "__main__":
    run_ingestion()
