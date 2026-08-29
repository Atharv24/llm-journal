import os
import json
import time
import requests
import subprocess
from pathlib import Path
from datetime import datetime
from db_utils import get_db

# --- CONFIGURATION ---
OBSIDIAN_VAULT = Path(r"D:\Obsidian\Mind Castle")
WHISPER_EXE_GPU = r"C:\Tools\whisper.cpp-gpu\whisper-cli.exe"
WHISPER_MODEL = r"C:\Tools\whisper.cpp\models\ggml-medium.en.bin"
OLLAMA_URL = "http://localhost:11434/api/generate"

def process_transcript_with_llm(raw_transcript):
    prompt = f"""
    You are an Obsidian assistant for me (Atharv Maan). Your task is to summarize and organize my thoughts.
    Analyze this voice transcript from me:
    "{raw_transcript}"

    Return ONLY a raw JSON object (no markdown, no preamble) matching this schema:
    {{
      "title": "Brief 3-5 word title",
      "summary": "1-2 sentence summary",
      "category": "Choose one: Projects, Areas, Resources, Thoughts",
      "tags": ["tag1", "tag2"],
      "wiki_links": ["[[Concept1]]", "[[Concept2]]"],
      "action_items": ["Action item 1", "Action item 2"]
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
            "summary": "AI processing failed.",
            "category": "Inbox",
            "tags": ["unprocessed"],
            "wiki_links": [],
            "action_items": []
        }

def transcribe_audio(audio_path):
    cmd = [WHISPER_EXE_GPU, "-m", WHISPER_MODEL, "-f", str(audio_path), "-nt", "-l", "en"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode("utf-8", errors="replace").strip()

def process_single_note(record_id, file_path_str):
    audio_path = Path(file_path_str)
    
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file no longer exists on disk: {audio_path}")

    print(f"\n[+] Transcribing: {audio_path.name}")
    raw_transcript = transcribe_audio(audio_path)
    
    if not raw_transcript:
        raise ValueError("Whisper returned an empty transcript.")

    print(f"[+] Enriching transcript via Ollama...")
    data = process_transcript_with_llm(raw_transcript)

    # Prepare Obsidian Output
    folder_prefix = data.get("category", "Inbox")
    target_dir = OBSIDIAN_VAULT / folder_prefix
    target_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    safe_title = "".join(c for c in data.get("title", "Voice Note") if c.isalnum() or c in (" ", "_", "-")).strip()
    file_name = f"{date_str} - {safe_title}.md"
    file_path = target_dir / file_name

    tags_formatted = "\n".join([f"  - {t}" for t in data.get("tags", [])])
    tasks_formatted = "\n".join([f"- [ ] {task}" for task in data.get("action_items", [])]) or "None extracted."
    links_formatted = " ".join(data.get("wiki_links", []))

    markdown = f"""---
type: voice-note
created: {date_str} {time_str}
source: "{audio_path.name}"
category: {data.get('category', 'Inbox')}
summary: {data.get('summary', '')}
tags:
  - voice-note
{tags_formatted}
---
# 🎙️ {data.get('title', 'Voice Note')}

> **Summary:** {data.get('summary', '')}
> **Related:** {links_formatted}

## 📋 Tasks
{tasks_formatted}

## 📝 Transcript
{raw_transcript}
"""

    file_path.write_text(markdown, encoding="utf-8")
    return raw_transcript, str(file_path)

def run_worker():
    print("[🚀 Worker Started] Polling queue for PENDING voice notes...")
    
    while True:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, file_path FROM voice_notes 
                WHERE status = 'PENDING' 
                ORDER BY created_at ASC 
                LIMIT 1
            """)
            row = cursor.fetchone()

            if not row:
                time.sleep(3)
                continue

            record_id, file_path_str = row["id"], row["file_path"]

            # Mark state to prevent duplicate picking
            cursor.execute("UPDATE voice_notes SET status = 'PROCESSING' WHERE id = ?", (record_id,))

        try:
            transcript, note_path = process_single_note(record_id, file_path_str)
            
            with get_db() as conn:
                conn.execute("""
                    UPDATE voice_notes 
                    SET status = 'COMPLETED', 
                        transcription = ?, 
                        processed_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (transcript, record_id))
            print(f"[✔ Successfully Created]: {note_path}")

        except Exception as e:
            print(f"[❌ Processing Failed]: {e}")
            with get_db() as conn:
                conn.execute("""
                    UPDATE voice_notes 
                    SET status = 'FAILED', 
                        error_log = ? 
                    WHERE id = ?
                """, (str(e), record_id))

if __name__ == "__main__":
    run_worker()
