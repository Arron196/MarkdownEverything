import json
import subprocess
from pathlib import Path
from typing import Protocol

import httpx

from app.config import settings


class AsrProvider(Protocol):
    def transcribe(self, audio_path: Path) -> dict:
        ...


def seconds_to_timestamp(value: float | int | None) -> str:
    seconds = int(value or 0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class LocalWhisperProvider:
    def transcribe(self, audio_path: Path) -> dict:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "Local ASR is not available. Install faster-whisper or use ASR_PROVIDER=cloud_openai_compatible with ASR_API_KEY."
            ) from exc
        model = WhisperModel(
            settings.local_whisper_model,
            device=settings.local_whisper_device,
            compute_type=settings.local_whisper_compute_type,
        )
        segments, info = model.transcribe(str(audio_path), vad_filter=True)
        normalized_segments = [
            {"start": segment.start, "end": segment.end, "text": segment.text}
            for segment in segments
        ]
        return {
            "language": getattr(info, "language", None),
            "duration": getattr(info, "duration", None),
            "text": " ".join(segment["text"].strip() for segment in normalized_segments).strip(),
            "segments": normalized_segments,
        }


class CloudOpenAICompatibleProvider:
    def transcribe(self, audio_path: Path) -> dict:
        api_key = settings.asr_api_key or settings.ai_api_key
        if not api_key:
            raise RuntimeError("ASR_API_KEY is required for cloud_openai_compatible ASR")
        base_url = (settings.asr_base_url or settings.ai_base_url).rstrip("/")
        with httpx.Client(timeout=180) as client:
            with audio_path.open("rb") as handle:
                response = client.post(
                    f"{base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (audio_path.name, handle, "application/octet-stream")},
                    data={"model": settings.asr_model, "response_format": "verbose_json"},
                )
        response.raise_for_status()
        return response.json()


def get_provider() -> AsrProvider:
    if settings.asr_provider == "cloud_openai_compatible":
        return CloudOpenAICompatibleProvider()
    return LocalWhisperProvider()


def normalize_transcript(payload: dict) -> tuple[list[dict[str, str]], str | None, str | None]:
    segments = payload.get("segments") or []
    timeline: list[dict[str, str]] = []
    for index, segment in enumerate(segments, start=1):
        timeline.append(
            {
                "start": seconds_to_timestamp(segment.get("start")),
                "end": seconds_to_timestamp(segment.get("end")),
                "title": f"片段 {index}",
                "text": (segment.get("text") or "").strip(),
            }
        )
    if not timeline and payload.get("text"):
        timeline.append({"start": "00:00", "end": seconds_to_timestamp(payload.get("duration")), "title": "转写", "text": payload["text"]})
    duration = None
    if timeline:
        duration = timeline[-1]["end"]
    language = payload.get("language")
    return timeline, language, duration


def probe_duration(media_path: Path) -> str | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(media_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        return seconds_to_timestamp(float(payload["format"]["duration"]))
    except Exception:
        return None
