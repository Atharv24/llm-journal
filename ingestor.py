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
    """Ensures iCloud download has finished and file isn't locked."""
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
    """Strips non-breaking spaces and invalid characters from filename."""
    original_name = file_path.name
    clean_name = original_name.replace('\u202f', ' ').replace('\u00a0', ' ')
    clean_name = re.sub(r'[^\x00-\x7F]+', '', clean_name).strip()

    if clean_name != original_name and clean_name:
        clean_path = file_path.parent / clean_name
        if clean_path.exists():
            return file_path
        try:
            file_path.rename(clean_path)
            print(f"  [✏️ Renamed]: '{original_name}' -> '{clean_name}'")
            return clean_path
        except OSError:
            return file_path
            
    return file_path

def register_file(conn, file_path: Path):
    if not wait_for_file_ready(file_path):
        return False, "skipped"

    try:
        mtime = os.path.getmtime(file_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT file_mtime, status FROM voice_notes WHERE file_path = ?", (str(file_path),))
        row = cursor.fetchone()
        
        # Skip unchanged files without hashing
        if row is not None and row["file_mtime"] == mtime:
            return False, "unchanged"

        f_hash = compute_hash(file_path)
        
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
        
        is_new = row is None
        return True, "new" if is_new else "modified"

    except Exception as e:
        print(f"❌ Error registering '{file_path.name}': {e}")
        return False, "error"

def run_ingestion():
    init_db()
    
    if not WATCH_DIR.exists():
        print(f"❌ Directory does not exist: {WATCH_DIR}")
        sys.exit(1)

    total_scanned = 0
    queued_count = 0

    with get_db() as conn:
        for root, _, files in os.walk(WATCH_DIR):
            for file in files:
                if file.lower().endswith(AUDIO_EXTENSIONS) and not file.endswith(".icloud") and ".tmp" not in file:
                    total_scanned += 1
                    raw_path = Path(root) / file
                    clean_path = normalize_and_rename(raw_path)
                    
                    is_queued, action_type = register_file(conn, clean_path)
                    if is_queued:
                        queued_count += 1
                        tag = "[+] New" if action_type == "new" else "[🔄 Modified]"
                        print(f"  {tag}: {clean_path.name}")

    if queued_count > 0:
        print(f"✅ Ingested {queued_count} new/updated file(s) out of {total_scanned} scanned.")
    
    return queued_count

if __name__ == "__main__":
    run_ingestion()
