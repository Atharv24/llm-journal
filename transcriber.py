import logging
import subprocess
from pathlib import Path

from config import WHISPER_EXE, WHISPER_LANG, WHISPER_MODEL

logger = logging.getLogger(__name__)


def transcribe_audio(audio_path: Path) -> str:
    """Transcribes audio using whisper.cpp CLI with returncode validation and detailed error reporting."""
    if not WHISPER_EXE.exists():
        raise FileNotFoundError(f"Whisper executable not found at: {WHISPER_EXE}")
    if not WHISPER_MODEL.exists():
        raise FileNotFoundError(f"Whisper model not found at: {WHISPER_MODEL}")

    cmd = [str(WHISPER_EXE), "-m", str(WHISPER_MODEL), "-f", str(audio_path), "-nt"]
    if WHISPER_LANG:
        cmd.extend(["-l", WHISPER_LANG])

    logger.info("   │  🎙️ Transcribing audio with Whisper GPU...")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if result.returncode != 0:
        err_msg = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Whisper CLI failed (exit code {result.returncode}): {err_msg}"
        )

    transcript = result.stdout.decode("utf-8", errors="replace").strip()
    if not transcript:
        raise ValueError(
            "Whisper completed successfully but returned an empty transcript."
        )

    return transcript
