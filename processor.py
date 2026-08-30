import json
import requests
import subprocess
from pathlib import Path
from datetime import datetime
from db_utils import get_db
from rag_utils import sync_vault_index_incremental, retrieve_relevant_context, add_note_to_vector_db

OBSIDIAN_VAULT = Path(r"D:\iCloudDrive\iCloud~md~obsidian\Mind Castle")
WHISPER_EXE_GPU = r"C:\Tools\whisper.cpp-gpu\whisper-cli.exe"
WHISPER_MODEL = r"C:\Tools\whisper.cpp\models\ggml-medium.en.bin"
OLLAMA_URL = "http://localhost:11434/api/generate"


def transcribe_audio(audio_path):
    cmd = [WHISPER_EXE_GPU, "-m", WHISPER_MODEL, "-f", str(audio_path), "-nt", "-l", "en"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode("utf-8", errors="replace").strip()


def process_transcript_with_llm(raw_transcript, rag_context):
    prompt = f"""
You are an Obsidian assistant. Your job is to structure and format my voice note.

--- Relevant Context From Existing Obsidian Vault Notes ---
{rag_context if rag_context else "No relevant existing notes found."}

--- Raw Audio Transcript ---
"{raw_transcript}"

Using the existing vault context:
1. Correct any misheard proper nouns or terms based on existing note names.
2. Formulate proper wiki links using `[[Note Title]]` format where applicable.

Return ONLY a raw JSON object matching this schema:
{{
  "title": "Brief 3-5 word title",
  "summary": "1-2 sentence summary",
  "category": "Choose one: Projects, Areas, Resources, Thoughts",
  "tags": ["tag1", "tag2"],
  "wiki_links": ["[[ExistingNoteTitle]]"],
  "action_items": ["Action item 1"]
}}
"""
    try:
        res = requests.post(OLLAMA_URL, json={
            "model": "gemma4",
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }, timeout=45)
        return json.loads(res.json()["response"])
    except Exception as e:
        print(f"  [-] LLM processing failed: {e}")
        return {
            "title": "Unprocessed Voice Note",
            "summary": "LLM processing failed.",
            "category": "Thoughts",
            "tags": ["unprocessed"],
            "wiki_links": [],
            "action_items": []
        }


def run_one_shot():
    # Sync vector index for any modified vault notes prior to processing
    sync_vault_index_incremental()

    processed_count = 0

    while True:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, file_path, status, transcription 
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
            status = row["status"]
            transcript = row["transcription"]

        try:
            # Stage 1: Audio Transcription
            if status == "PENDING":
                print(f"\n[🎙️ Transcribing]: {file_path.name}")
                transcript = transcribe_audio(file_path)
                if not transcript:
                    raise ValueError("Whisper returned an empty transcript.")
                
                with get_db() as conn:
                    conn.execute("""
                        UPDATE voice_notes 
                        SET status = 'TRANSCRIBED', transcription = ? 
                        WHERE id = ?
                    """, (transcript, record_id))
                status = "TRANSCRIBED"

            # Stage 2: RAG Retrieval + LLM Generation + Vault Embed
            if status == "TRANSCRIBED":
                print(f"[🧠 RAG & LLM Processing]: Querying vector context and calling Gemma4...")
                rag_context = retrieve_relevant_context(transcript, top_k=3)
                llm_data = process_transcript_with_llm(transcript, rag_context)

                # Format and Write Markdown file
                category = llm_data.get("category", "Thoughts")
                target_dir = OBSIDIAN_VAULT / category
                target_dir.mkdir(parents=True, exist_ok=True)

                now = datetime.now()
                date_str = now.strftime("%Y-%m-%d")
                safe_title = "".join(c for c in llm_data.get("title", "Voice Note") if c.isalnum() or c in (" ", "_", "-")).strip()
                out_path = target_dir / f"{date_str} - {safe_title}.md"

                tags_fmt = "\n".join([f"  - {t}" for t in llm_data.get("tags", [])])
                tasks_fmt = "\n".join([f"- [ ] {task}" for task in llm_data.get("action_items", [])]) or "None extracted."
                links_fmt = " ".join(llm_data.get("wiki_links", []))

                md_content = f"""---
type: voice-note
created: {date_str}
source: "{file_path.name}"
summary: "{llm_data.get('summary', '')}"
tags:
  - voice-note
{tags_fmt}
---
# 🎙️ {llm_data.get('title', 'Voice Note')}

> **Summary:** {llm_data.get('summary', '')}
> **Related:** {links_fmt}

## 📋 Action Items
{tasks_fmt}

## 📝 Transcript
{transcript}
"""
                out_path.write_text(md_content, encoding="utf-8")

                # Embed newly created note into ChromaDB immediately
                add_note_to_vector_db(out_path)

                with get_db() as conn:
                    conn.execute("""
                        UPDATE voice_notes 
                        SET status = 'COMPLETED', 
                            summary_json = ?, 
                            obsidian_path = ?, 
                            processed_at = CURRENT_TIMESTAMP 
                        WHERE id = ?
                    """, (json.dumps(llm_data), str(out_path), record_id))

                print(f"✔ [Created Note]: {out_path.name}")
                processed_count += 1

        except Exception as e:
            print(f"❌ Processing failed for record {record_id}: {e}")
            with get_db() as conn:
                conn.execute("""
                    UPDATE voice_notes 
                    SET status = 'FAILED', error_log = ? 
                    WHERE id = ?
                """, (str(e), record_id))

    return processed_count

if __name__ == "__main__":
    run_one_shot()
