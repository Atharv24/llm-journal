import os
import re
import time
import json
import subprocess
import requests
from datetime import datetime
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --- CONFIGURATION ---
WATCH_DIR = r"D:\iCloudDrive\voice notes"
OBSIDIAN_VAULT = r"D:\Obsidian\Mind Castle"

WHISPER_EXE = r"C:\Tools\whisper.cpp\whisper-cli.exe"
WHISPER_EXE_GPU = r"C:\Tools\whisper.cpp-gpu\whisper-cli.exe"
WHISPER_MODEL = r"C:\Tools\whisper.cpp\models\ggml-medium.en.bin"
OLLAMA_URL = "http://localhost:11434/api/generate"

processed_files = set()

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
        }, timeout=30)
        return json.loads(res.json()["response"])
    except Exception as e:
        print(f"[-] LLM processing failed: {e}")
        return {
            "title": "Unprocessed Voice Note",
            "summary": "AI processing failed.",
            "category": "Inbox",
            "tags": ["unprocessed"],
            "wiki_links": [],
            "action_items": []
        }

def process_audio(audio_path):
    if audio_path in processed_files:
        return
        
    print(f"\n[+] Processing file: {audio_path}")
    
    if not wait_for_file_ready(audio_path):
        print("[-] File still locked or downloading via iCloud. Skipping for now.")
        return
    path = Path(audio_path)
    # Remove non-ASCII characters (e.g., U+202F)
    clean_name = re.sub(r'[^\x00-\x7F]+', '', path.name)
            
    if clean_name != path.name:
        new_path = path.parent / clean_name
        try:
            path.rename(new_path)
            audio_path = path.name
            print(f"Renamed: {path.name} -> {clean_name}")
        except OSError as e:
            print(f"Error renaming file: {e}")

    # Mark as processed so modified events don't double-trigger
    processed_files.add(audio_path)

    # 1. Transcribe via whisper.cpp with safe decoding fallback
    cmd = [WHISPER_EXE_GPU, "-m", WHISPER_MODEL, "-f", audio_path, "-nt", "-l", "en"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Safely decode bytes (replaces non-utf8 characters like 0xa0)
    raw_transcript = result.stdout.decode("utf-8", errors="replace").strip()
    
    if not raw_transcript:
        print("[-] Transcription returned empty.")
        return

    # 2. Enrich via Local LLM
    data = process_transcript_with_llm(raw_transcript)

    # 3. Route to Obsidian
    folder_prefix = data.get("category")
    target_dir = os.path.join(OBSIDIAN_VAULT, folder_prefix)
    os.makedirs(target_dir, exist_ok=True)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    safe_title = "".join(c for c in data.get("title", "Voice Note") if c.isalnum() or c in (" ", "_", "-")).strip()
    file_name = f"{date_str} - {safe_title}.md"
    file_path = os.path.join(target_dir, file_name)

    tags_formatted = "\n".join([f"  - {t}" for t in data.get("tags", [])])
    tasks_formatted = "\n".join([f"- [ ] {task}" for task in data.get("action_items", [])]) or "None extracted."
    links_formatted = " ".join(data.get("wiki_links", []))

    markdown = f"""---
type: voice-note
created: {date_str} {time_str}
source: "{os.path.basename(audio_path)}"
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

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"[✔] Successfully created note: {file_path}")

class AudioHandler(FileSystemEventHandler):
    def handle_event(self, event):
        if not event.is_directory and event.src_path.lower().endswith(('.m4a', '.wav', '.mp3')):
            # Ignore temporary iCloud download files (.tmp, .icloud)
            if ".tmp" in event.src_path or event.src_path.endswith(".icloud"):
                return
            process_audio(event.src_path)

    def on_created(self, event):
        self.handle_event(event)

    def on_modified(self, event):
        self.handle_event(event)

    def on_moved(self, event):
        # Trigger when iCloud renames a temporary sync file to the actual audio filename
        if hasattr(event, 'dest_path'):
            event.src_path = event.dest_path
            self.handle_event(event)

if __name__ == "__main__":
    observer = Observer()
    observer.schedule(AudioHandler(), WATCH_DIR, recursive=False)
    observer.start()
    print(f"[*] Watching {WATCH_DIR}...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()