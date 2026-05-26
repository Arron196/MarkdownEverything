import importlib.util
import shutil

from app.config import settings


def get_capabilities() -> dict:
    return {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffprobe": shutil.which("ffprobe") is not None,
        "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
        "local_asr": importlib.util.find_spec("faster_whisper") is not None,
        "cloud_asr_configured": bool((settings.asr_api_key or settings.ai_api_key) and settings.asr_provider == "cloud_openai_compatible"),
        "ai_summary_configured": bool(settings.ai_api_key),
        "asr_provider": settings.asr_provider,
    }
