import json
import logging
import yaml
from pathlib import Path
from datetime import datetime
from typing import Optional

from db_utils import get_db, get_records_for_reprocessing
from rag_utils import (
    sync_vault_index_incremental,
    retrieve_relevant_context,
    add_note_to_vector_db,
    remove_note_from_vector_db
)
from transcriber import transcribe_audio
from llm_parser import process_transcript_with_llm, normalize_wiki_links, normalize_tags
from config import (
    OBSIDIAN_VAULT,
    OLLAMA_MODEL,
    NOTE_NAME_FORMAT,
    DATE_FORMAT,
    TIME_FORMAT
)

logger = logging.getLogger(__name__)


def get_unique_note_path(target_dir: Path, base_name: str, current_path: Optional[Path] = None) -> Path:
    """Generates a non-conflicting markdown filepath. Reuses current_path if name matches."""
    candidate = target_dir / f"{base_name}.md"
    if current_path and candidate == current_path:
        return candidate

    counter = 1
    while candidate.exists() and candidate != current_path:
        candidate = target_dir / f"{base_name} ({counter}).md"
        counter += 1
    return candidate


def generate_note_file(
    file_path: Path,
    file_mtime: float,
    transcript: str,
    llm_data: dict,
    existing_obsidian_path: Optional[Path] = None,
    dry_run: bool = False
) -> Path:
    """Formats markdown and saves/updates the note in Obsidian vault."""
    category = llm_data.get("category", "Thoughts")
    target_dir = OBSIDIAN_VAULT / category
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    # Determine recording timestamp from audio file mtime
    try:
        rec_time = datetime.fromtimestamp(file_mtime if file_mtime else file_path.stat().st_mtime)
    except Exception:
        rec_time = datetime.now()

    date_str = rec_time.strftime(DATE_FORMAT)
    time_str = rec_time.strftime(TIME_FORMAT)
    time_display = rec_time.strftime("%H:%M")

    raw_title = llm_data.get("title", "Voice Note").strip()
    safe_title = "".join(c for c in raw_title if c.isalnum() or c in (" ", "_", "-")).strip() or "Voice Note"

    # Construct formatted file name
    base_name = NOTE_NAME_FORMAT.format(
        date=date_str,
        time=time_str,
        category=category,
        title=safe_title
    )
    out_path = get_unique_note_path(target_dir, base_name, current_path=existing_obsidian_path)

    # Prepare links formatting
    formatted_links = normalize_wiki_links(llm_data.get("wiki_links", []))
    links_fmt = " ".join(formatted_links) if formatted_links else "None."

    # Prepare rich frontmatter with Aliases for instant Obsidian Quick Switcher search
    aliases = list(dict.fromkeys([
        raw_title,
        f"{safe_title}",
        f"{date_str} {safe_title}"
    ]))

    frontmatter_dict = {
        "type": "voice-note",
        "created": f"{date_str} {time_display}",
        "source": file_path.name,
        "category": category,
        "summary": llm_data.get("summary", ""),
        "aliases": aliases,
        "related": formatted_links if formatted_links else [],
        "tags": normalize_tags(["voice-note", category] + llm_data.get("tags", []))
    }
    frontmatter_yaml = yaml.safe_dump(frontmatter_dict, sort_keys=False, allow_unicode=True).strip()

    tasks_fmt = "\n".join([f"- [ ] {task}" for task in llm_data.get("action_items", [])]) or "None extracted."

    md_content = f"""---
{frontmatter_yaml}
---
# 🎙️ {raw_title}

> **Summary:** {llm_data.get('summary', '')}
> **Category:** [[{category}]] | **Recorded:** `{date_str} {time_display}`
> **Related:** {links_fmt}

## 📋 Action Items
{tasks_fmt}

## 📝 Transcript
{transcript}
"""
    if dry_run:
        logger.info(f"  [DRY-RUN Preview]: Would write '{out_path.name}' (Category: {category}, Related: {links_fmt})")
        return out_path

    # If the file path changed (e.g. category or title was updated), delete the old file and its vector embedding
    if existing_obsidian_path and existing_obsidian_path.exists() and existing_obsidian_path != out_path:
        try:
            remove_note_from_vector_db(existing_obsidian_path)
            existing_obsidian_path.unlink()
            logger.info(f"  [Moved/Renamed]: Removed previous note '{existing_obsidian_path.name}'")
        except OSError as e:
            logger.warning(f"Could not delete old note file '{existing_obsidian_path}': {e}")

    out_path.write_text(md_content, encoding="utf-8")
    add_note_to_vector_db(out_path)
    return out_path


def run_one_shot() -> int:
    """Executes one-shot processing on all pending voice notes in SQLite queue."""
    sync_vault_index_incremental()

    processed_count = 0

    while True:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, file_path, file_mtime, status, transcription 
                FROM voice_notes 
                WHERE status IN ('PENDING', 'TRANSCRIBED')
                ORDER BY created_at ASC 
                LIMIT 1
            """)
            row = cursor.fetchone()

            if not row:
                break

            record_id = row["id"]
            file_path = Path(row["file_path"])
            file_mtime = row["file_mtime"]
            status = row["status"]
            transcript = row["transcription"]

        try:
            # Stage 1: Audio Transcription
            if status == "PENDING":
                transcript = transcribe_audio(file_path)
                with get_db() as conn:
                    conn.execute("""
                        UPDATE voice_notes 
                        SET status = 'TRANSCRIBED', transcription = ? 
                        WHERE id = ?
                    """, (transcript, record_id))
                status = "TRANSCRIBED"

            # Stage 2: RAG Retrieval + LLM Generation + Vault Write
            if status == "TRANSCRIBED":
                logger.info(f"[🧠 RAG & LLM Processing]: Querying vector context and calling {OLLAMA_MODEL}...")
                rag_context = retrieve_relevant_context(transcript)
                llm_data = process_transcript_with_llm(transcript, rag_context)

                out_path = generate_note_file(
                    file_path=file_path,
                    file_mtime=file_mtime,
                    transcript=transcript,
                    llm_data=llm_data
                )

                with get_db() as conn:
                    conn.execute("""
                        UPDATE voice_notes 
                        SET status = 'COMPLETED', 
                            summary_json = ?, 
                            obsidian_path = ?, 
                            processed_at = CURRENT_TIMESTAMP 
                        WHERE id = ?
                    """, (json.dumps(llm_data), str(out_path), record_id))

                logger.info(f"✔ [Created Note]: {out_path.name}")
                processed_count += 1

        except Exception as e:
            logger.error(f"❌ Processing failed for record {record_id} ({file_path.name}): {e}", exc_info=True)
            with get_db() as conn:
                conn.execute("""
                    UPDATE voice_notes 
                    SET status = 'FAILED', error_log = ? 
                    WHERE id = ?
                """, (str(e), record_id))

    return processed_count


def reprocess_notes(record_id: Optional[int] = None, dry_run: bool = False) -> int:
    """Re-synthesizes notes using their cached transcripts with latest LLM prompts & RAG context."""
    sync_vault_index_incremental()

    records = get_records_for_reprocessing(record_id=record_id)
    if not records:
        logger.info("✨ No records found to reprocess.")
        return 0

    mode_str = "[DRY-RUN] " if dry_run else ""
    logger.info(f"🔄 {mode_str}Reprocessing {len(records)} note(s) with latest RAG context & LLM prompt...")

    reprocessed_count = 0

    for row in records:
        rec_id = row["id"]
        file_path = Path(row["file_path"])
        file_mtime = row["file_mtime"]
        transcript = row["transcription"]
        existing_obsidian_path = Path(row["obsidian_path"]) if row["obsidian_path"] else None

        logger.info(f"\n[🔄 Reprocessing Record #{rec_id}]: {file_path.name}")

        try:
            rag_context = retrieve_relevant_context(transcript)
            llm_data = process_transcript_with_llm(transcript, rag_context)

            out_path = generate_note_file(
                file_path=file_path,
                file_mtime=file_mtime,
                transcript=transcript,
                llm_data=llm_data,
                existing_obsidian_path=existing_obsidian_path,
                dry_run=dry_run
            )

            if not dry_run:
                with get_db() as conn:
                    conn.execute("""
                        UPDATE voice_notes 
                        SET status = 'COMPLETED', 
                            summary_json = ?, 
                            obsidian_path = ?, 
                            processed_at = CURRENT_TIMESTAMP 
                        WHERE id = ?
                    """, (json.dumps(llm_data), str(out_path), rec_id))

                logger.info(f"✔ [Updated Note]: {out_path.name}")
            reprocessed_count += 1

        except Exception as e:
            logger.error(f"❌ Failed reprocessing record #{rec_id}: {e}", exc_info=True)

    return reprocessed_count
