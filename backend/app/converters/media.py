import subprocess
import shutil
from pathlib import Path

from yt_dlp import YoutubeDL

from app.converters.base import ConversionResult
from app.services.asr import get_provider, normalize_transcript, probe_duration
from app.services.url_security import assert_public_url


AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


def extract_audio(video_path: Path, output_dir: Path) -> Path:
    ensure_command("ffmpeg", "Video conversion requires ffmpeg. Install ffmpeg or use the Docker Compose deployment.")
    audio_path = output_dir / f"{video_path.stem}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(audio_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return audio_path


def download_video_audio(url: str, output_dir: Path) -> tuple[Path, dict]:
    assert_public_url(url)
    ensure_command("yt-dlp", "Video URL conversion requires yt-dlp. Install yt-dlp or use the Docker Compose deployment.")
    ensure_command("ffmpeg", "Video URL conversion requires ffmpeg. Install ffmpeg or use the Docker Compose deployment.")
    output_template = str(output_dir / "download.%(ext)s")
    options = {
        "outtmpl": output_template,
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav", "preferredquality": "192"}],
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    downloaded = output_dir / "download.wav"
    if not downloaded.exists():
        candidates = sorted(output_dir.glob("download.*"))
        if not candidates:
            raise RuntimeError("yt-dlp did not produce a downloadable media file")
        downloaded = candidates[0]
    return downloaded, info


def ensure_command(command: str, message: str) -> None:
    if shutil.which(command) is None:
        raise RuntimeError(message)


def convert_audio(path: Path) -> ConversionResult:
    provider = get_provider()
    payload = provider.transcribe(path)
    timeline, language, duration = normalize_transcript(payload)
    duration = duration or probe_duration(path)
    body = "\n\n".join(segment["text"] for segment in timeline)
    return ConversionResult(
        title=path.stem,
        source_type="audio",
        timeline=timeline,
        summary_seed=body,
        language=language,
        duration=duration,
    )


def convert_video_file(path: Path, work_dir: Path) -> ConversionResult:
    audio_path = extract_audio(path, work_dir)
    result = convert_audio(audio_path)
    result.title = path.stem
    result.source_type = "video"
    result.duration = result.duration or probe_duration(path)
    return result


def convert_video_url(url: str, work_dir: Path) -> ConversionResult:
    audio_path, info = download_video_audio(url, work_dir)
    result = convert_audio(audio_path)
    result.title = info.get("title") or "视频链接"
    result.source_type = "video"
    result.source_url = url
    if info.get("duration") and not result.duration:
        from app.services.asr import seconds_to_timestamp

        result.duration = seconds_to_timestamp(info["duration"])
    result.metadata.update({"extractor": info.get("extractor"), "webpage_url": info.get("webpage_url")})
    return result
