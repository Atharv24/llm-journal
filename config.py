import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure console streams support UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Load optional .env file located in the project root
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Database & ChromaDB storage
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "voice_notes.db")).resolve()
CHROMA_PATH = Path(os.getenv("CHROMA_PATH", BASE_DIR / "chroma_db")).resolve()

# Directories
WATCH_DIR = Path(os.getenv("WATCH_DIR", r"D:\iCloudDrive\voice notes")).resolve()
OBSIDIAN_VAULT = Path(os.getenv("OBSIDIAN_VAULT", r"D:\iCloudDrive\iCloud~md~obsidian\Mind Castle")).resolve()

# Whisper Configuration
WHISPER_EXE = Path(os.getenv("WHISPER_EXE", r"C:\Tools\whisper.cpp-gpu\whisper-cli.exe")).resolve()
WHISPER_MODEL = Path(os.getenv("WHISPER_MODEL", r"C:\Tools\whisper.cpp\models\ggml-medium.en.bin")).resolve()
WHISPER_LANG = os.getenv("WHISPER_LANG", "en")

# Ollama LLM & Embeddings Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))

# Ingestion & RAG Settings
AUDIO_EXTENSIONS = ('.m4a', '.wav', '.mp3', '.ogg', '.flac')
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
RAG_MAX_DISTANCE = float(os.getenv("RAG_MAX_DISTANCE", "0.60"))
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "obsidian_vault")

# Note Naming & Formatting
# Supported tokens: {date}, {time}, {category}, {title}
NOTE_NAME_FORMAT = os.getenv("NOTE_NAME_FORMAT", "{date} {time} - {title}")
DATE_FORMAT = os.getenv("DATE_FORMAT", "%Y-%m-%d")
TIME_FORMAT = os.getenv("TIME_FORMAT", "%H%M")
