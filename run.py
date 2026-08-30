import sys
import logging
import argparse
from db_utils import init_db, reset_failed_records, reset_all_for_reprocessing, list_records

# Ensure standard streams handle UTF-8 properly on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from ingestor import run_ingestion
    from processor import run_one_shot, reprocess_notes
    from rag_utils import sync_vault_index_incremental
except ImportError as e:
    print(f"[x FATAL] Failed to import pipeline modules: {e}")
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Whisper-Watcher: Automated Voice Notes Ingestion, Transcription, Obsidian RAG & Synthesis"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all audio files and their database SQLite IDs and statuses."
    )
    parser.add_argument(
        "--search",
        type=str,
        metavar="QUERY",
        help="Search for notes and find their SQLite ID by keyword or filename."
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Re-synthesize all existing notes with latest RAG context & LLM prompts using stored transcripts."
    )
    parser.add_argument(
        "--reprocess-id",
        type=int,
        metavar="ID",
        help="Re-synthesize a specific note record by its database ID."
    )
    parser.add_argument(
        "--retranscribe-all",
        action="store_true",
        help="Hard reset: force re-running Whisper audio transcription from scratch on all files."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate processing/reprocessing without writing notes to the Obsidian vault."
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Reset and retry audio files that previously failed processing."
    )
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        help="Only run file discovery and database queuing."
    )
    parser.add_argument(
        "--process-only",
        action="store_true",
        help="Only run transcription, RAG, and LLM processing on queued files."
    )
    parser.add_argument(
        "--sync-vault",
        action="store_true",
        help="Only synchronize the Obsidian vault vector index."
    )
    parser.add_argument(
        "--query-vault-detailed",
        type=str,
        metavar="QUERY",
        help="Search for notes and find their index by keyword or filename."
    )
    parser.add_argument(
        "--vector-index-stats",
        action="store_true",
        help="Shows the stats for Obsidian vault vector index."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug level logging output."
    )
    return parser.parse_args()


def display_records(query=None):
    records = list_records(search_query=query)
    if not records:
        print("No matching voice note records found in database.")
        return

    print("\n" + "=" * 105)
    print(f"{'ID':<5} | {'STATUS':<11} | {'OBSIDIAN NOTE': <42} | {'AUDIO FILE'}")
    print("-" * 105)
    for r in records:
        audio_name = r["file_path"].replace("\\", "/").split("/")[-1]
        note_name = r["obsidian_path"].replace("\\", "/").split("/")[-1] if r["obsidian_path"] else "-"
        if len(note_name) > 40:
            note_name = note_name[:37] + "..."
        if len(audio_name) > 37:
            audio_name = note_name[:34] + "..."
        print(f"#{r['id']:<4} | {r['status']:<11} | {note_name: <42} | {audio_name}")
    print("=" * 105 + "\n")


def main():
    args = parse_args()

    # Mode: List or search records
    if args.list or args.search:
        init_db()
        display_records(query=args.search)
        sys.exit(0)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    init_db()

    # Mode 1: Retranscribe all audio files
    if args.retranscribe_all:
        count = reset_all_for_reprocessing(retranscribe=True)
        logging.info(f"🔄 Reset {count} records for full audio re-transcription.")
        notes_created = run_one_shot()
        logging.info(f"🎉 Completed re-transcription and synthesis for {notes_created} notes.")
        sys.exit(0)

    # Mode 2: Reprocess existing notes using cached transcripts
    if args.reprocess or args.reprocess_id is not None:
        reprocessed = reprocess_notes(record_id=args.reprocess_id, dry_run=args.dry_run)
        logging.info(f"🎉 Reprocessing complete. Updated {reprocessed} note(s).")
        sys.exit(0)

    # Mode 3: Retry failed records
    if args.retry_failed:
        reset_count = reset_failed_records()
        logging.info(f"🔄 Reset {reset_count} failed record(s) back to PENDING.")

    # Mode 4: Sync vault vector index only
    if args.sync_vault:
        logging.info("🧠 Synchronizing Obsidian vault vector index...")
        sync_vault_index_incremental()
        sys.exit(0)

    new_queued = 0
    notes_created = 0

    # Stage 1: File Ingestion
    if not args.process_only:
        logging.info("🚀 [Stage 1/2] Voice Notes Ingestion...")
        try:
            new_queued = run_ingestion(retry_failed=args.retry_failed)
        except Exception as e:
            logging.error(f"❌ Ingestion failed: {e}", exc_info=True)
            sys.exit(2)

    if args.ingest_only:
        logging.info(f"\n🎉 Ingestion complete. Queued: {new_queued}")
        sys.exit(0)

    # Stage 2: Processing (Whisper + RAG + Ollama)
    logging.info("🚀 [Stage 2/2] Transcription & Knowledge Synthesis...")
    try:
        notes_created = run_one_shot()
    except Exception as e:
        logging.error(f"❌ Processing failed: {e}", exc_info=True)
        sys.exit(3)

    if (new_queued or 0) == 0 and (notes_created or 0) == 0:
        logging.info("   └── ✨ No pending audio files to process. Pipeline idle.")
    else:
        logging.info(f"🎉 Pipeline Complete. Queued: {new_queued or 0} | Processed: {notes_created or 0}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
