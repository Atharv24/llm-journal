# 🎙️ Whisper-Watcher

An automated pipeline for ingesting raw voice notes (e.g. from iCloud Drive), transcribing them locally using GPU-accelerated `whisper.cpp`, performing semantic context retrieval (RAG) on an existing Obsidian vault via ChromaDB & Ollama (`nomic-embed-text`), and generating structured Obsidian Markdown notes with `gemma4`.

---

## 🛠️ Architecture & Modules

```
[ iCloud Voice Notes ]
          │
          ▼
   [ ingestor.py ] ────► [ SQLite: voice_notes.db ]
                                   │
                                   ▼
 [ Obsidian Vault ] ────► [ rag_utils.py (ChromaDB) ] 
                                   │
                                   ▼
                            [ processor.py ]
                             ├─► [ transcriber.py ] (whisper.cpp GPU)
                             └─► [ llm_parser.py ]  (Ollama gemma4)
                                   │
                                   ▼
                         [ Obsidian Note .md ]
```

| Module | Responsibility |
| :--- | :--- |
| **`run.py`** | Main entrypoint & CLI dispatcher. |
| **`config.py`** | Central configuration with `.env` overrides. |
| **`db_utils.py`** | SQLite schema, status tracker, and reprocessing helpers. |
| **`ingestor.py`** | Audio discovery, hash deduplication, and file queuing. |
| **`transcriber.py`** | GPU-accelerated audio transcription via `whisper.cpp`. |
| **`rag_utils.py`** | Vector indexing, stale embedding cleanup, and cosine similarity retrieval. |
| **`llm_parser.py`** | Prompt construction, Ollama generation, and wikilink normalization. |
| **`processor.py`** | Note writing, unique naming, and vault backfill orchestration. |

---

## 📦 Requirements & Installation

1. **Python 3.10+**
2. **Local Ollama** with models pulled:
   ```bash
   ollama pull gemma4
   ollama pull nomic-embed-text
   ```
3. **Whisper CLI (`whisper.cpp`)** with a compatible model (e.g., `ggml-medium.en.bin`).

### Setup Python Environment
```bash
pip install -r requirements.txt
```

---

## ⚙️ Configuration

Copy `.env.example` to `.env` to customize settings or paths:
```ini
WATCH_DIR=D:\iCloudDrive\voice notes
OBSIDIAN_VAULT=D:\iCloudDrive\iCloud~md~obsidian\Mind Castle
WHISPER_EXE=C:\Tools\whisper.cpp-gpu\whisper-cli.exe
WHISPER_MODEL=C:\Tools\whisper.cpp\models\ggml-medium.en.bin
WHISPER_LANG=en
OLLAMA_MODEL=gemma4
EMBED_MODEL=nomic-embed-text
NOTE_NAME_FORMAT={date} {time} - {title}
```

---

## 🚀 Usage

### Standard Pipeline Run
```bash
python run.py
```

### Backfill & Reprocessing Existing Notes
When you make improvements to prompts, metadata formatting, or RAG linking, update existing notes instantly without re-running audio transcription:
```bash
# Reprocess all existing notes using cached transcripts
python run.py --reprocess

# Dry-run preview without modifying files
python run.py --reprocess --dry-run

# Reprocess a single specific note by SQLite ID
python run.py --reprocess-id 5

# Force full re-transcription with Whisper GPU from scratch
python run.py --retranscribe-all
```

### Other Useful Commands
- `--retry-failed`: Retry any recordings that previously failed.
- `--sync-vault`: Re-index Obsidian vault vector embeddings.
- `--ingest-only`: Scan and queue files without transcribing.
- `--process-only`: Process queued files without scanning for new audio.
- `-v, --verbose`: Enable debug logs.
