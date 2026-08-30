import os
import re
import sys
import time
import hashlib
import logging
from pathlib import Path
from db_utils import get_db, init_db
from config import WATCH_DIR, AUDIO_EXTENSIONS

logger = logging.getLogger(__name__)

def wait_for_file_ready(filepath: Path, timeout: int = 15) -> bool:
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

def compute_hash(file_path: Path) -> str:
    """Computes SHA-256 hash of the given file."""
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def normalize_and_rename(file_path: Path) -> Path:
    """Strips non-breaking spaces and invalid Windows filename characters."""
    original_name = file_path.name
    # Replace non-breaking spaces
    clean_name = original_name.replace('\u202f', ' ').replace('\u00a0', ' ')
    # Remove Windows invalid characters: < > : " / \ | ? * and control chars
    clean_name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '', clean_name).strip()

    if clean_name != original_name and clean_name:
        clean_path = file_path.parent / clean_name
        if clean_path.exists():
            return file_path
        try:
            file_path.rename(clean_path)
            logger.info(f"  [Renamed]: '{original_name}' -> '{clean_name}'")
            return clean_path
        except OSError as e:
            logger.warning(f"  [Rename Skipped] Could not rename '{original_name}': {e}")
            return file_path
            
    return file_path

def register_file(conn, file_path: Path, retry_failed: bool = False) -> tuple[bool, str]:
    """Registers an audio file into SQLite DB. Returns (is_queued, action_type)."""
    if not wait_for_file_ready(file_path):
        return False, "skipped"

    try:
        mtime = os.path.getmtime(file_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT file_hash, file_mtime, status FROM voice_notes WHERE file_path = ?", (str(file_path),))
        row = cursor.fetchone()
        
        # Skip unchanged files unless retry_failed is requested for failed notes
        if row is not None:
            if row["file_mtime"] == mtime and not (retry_failed and row["status"] == "FAILED"):
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
                    WHEN ? = 1 AND voice_notes.status = 'FAILED' THEN 'PENDING'
                    ELSE voice_notes.status 
                END
        """, (str(file_path), f_hash, mtime, 1 if retry_failed else 0))
        
        is_new = row is None
        action_type = "new" if is_new else "modified"
        return True, action_type

    except Exception as e:
        logger.error(f"❌ Error registering '{file_path.name}': {e}", exc_info=True)
        return False, "error"

def run_ingestion(retry_failed: bool = False) -> int:
    """Scans watch directory and enqueues audio files for processing."""
    init_db()
    
    if not WATCH_DIR.exists():
        logger.error(f"❌ Watch directory does not exist: {WATCH_DIR}")
        return 0

    total_scanned = 0
    queued_count = 0

    with get_db() as conn:
        for root, _, files in os.walk(WATCH_DIR):
            for file in files:
                if file.lower().endswith(AUDIO_EXTENSIONS) and not file.endswith(".icloud") and ".tmp" not in file:
                    total_scanned += 1
                    raw_path = Path(root) / file
                    clean_path = normalize_and_rename(raw_path)
                    
                    is_queued, action_type = register_file(conn, clean_path, retry_failed=retry_failed)
                    if is_queued:
                        queued_count += 1
                        tag = "[+] New" if action_type == "new" else "[🔄 Modified]"
                        logger.info(f"   ├── {tag}: {clean_path.name}")

    if queued_count > 0:
        logger.info(f"   └── ✅ Queued {queued_count} new/updated file(s) (Scanned: {total_scanned}).")
    else:
        logger.info(f"   └── ✨ No new audio files found (Scanned: {total_scanned}).")
    
    return queued_count

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_ingestion()
