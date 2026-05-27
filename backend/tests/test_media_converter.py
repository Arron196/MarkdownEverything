from pathlib import Path
import subprocess

from app.converters import media
from app.services.asr import normalize_transcript


def test_normalize_transcript_merges_short_segments():
    payload = {
        "language": "zh",
        "segments": [
            {"start": 0, "end": 4, "text": "第一句话。"},
            {"start": 4, "end": 8, "text": "第二句话继续说明。"},
            {"start": 8, "end": 12, "text": "第三句话补充上下文。"},
        ],
    }

    timeline, language, duration = normalize_transcript(payload)

    assert language == "zh"
    assert duration == "00:12"
    assert len(timeline) == 1
    assert timeline[0]["start"] == "00:00"
    assert timeline[0]["end"] == "00:12"
    assert "第二句话继续说明" in timeline[0]["text"]


def test_convert_audio_adds_body_and_metadata(monkeypatch, tmp_path: Path):
    path = tmp_path / "voice.wav"
    path.write_bytes(b"fake")

    class Provider:
        def transcribe(self, _path):
            return {
                "language": "en",
                "duration": 8,
                "segments": [
                    {"start": 0, "end": 4, "text": "Hello world."},
                    {"start": 4, "end": 8, "text": "This is a test."},
                ],
            }

    monkeypatch.setattr(media, "get_provider", lambda: Provider())
    monkeypatch.setattr(media, "probe_duration", lambda _path: "00:08")

    result = media.convert_audio(path)

    assert result.body == "Hello world. This is a test."
    assert result.timeline[0]["start"] == "00:00"
    assert result.language == "en"
    assert result.metadata["converter"] == "audio"
    assert result.metadata["segment_count"] == 1


def test_convert_video_file_updates_metadata(monkeypatch, tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"audio")

    def fake_convert_audio(_path):
        return media.ConversionResult(
            title="clip",
            source_type="audio",
            body="Transcript",
            timeline=[{"start": "00:00", "end": "00:03", "title": "片段 1", "text": "Transcript"}],
            duration="00:03",
            metadata={"converter": "audio"},
        )

    monkeypatch.setattr(media, "extract_audio", lambda _path, _work_dir: audio)
    monkeypatch.setattr(media, "convert_audio", fake_convert_audio)

    result = media.convert_video_file(video, tmp_path)

    assert result.source_type == "video"
    assert result.title == "clip"
    assert result.metadata["converter"] == "video_file"
    assert result.metadata["extracted_audio"] == "clip.wav"


def test_convert_video_url_adds_public_media_metadata(monkeypatch, tmp_path: Path):
    audio = tmp_path / "download.wav"
    audio.write_bytes(b"audio")

    def fake_convert_audio(_path):
        return media.ConversionResult(
            title="download",
            source_type="audio",
            body="Transcript",
            timeline=[{"start": "00:00", "end": "00:03", "title": "片段 1", "text": "Transcript"}],
            metadata={"converter": "audio"},
        )

    monkeypatch.setattr(
        media,
        "download_video_audio",
        lambda _url, _work_dir: (
            audio,
            {"title": "Public Video", "duration": 3, "extractor": "Example", "webpage_url": "https://example.com/watch"},
        ),
    )
    monkeypatch.setattr(media, "convert_audio", fake_convert_audio)

    result = media.convert_video_url("https://example.com/watch", tmp_path)

    assert result.title == "Public Video"
    assert result.source_url == "https://example.com/watch"
    assert result.duration == "00:03"
    assert result.metadata["converter"] == "video_url"
    assert result.metadata["extractor"] == "Example"


def test_extract_audio_reports_ffmpeg_error(monkeypatch, tmp_path: Path):
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"fake")
    monkeypatch.setattr(media, "ensure_command", lambda _command, _message: None)

    def fail_run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "ffmpeg", stderr="Invalid data found when processing input")

    monkeypatch.setattr(media.subprocess, "run", fail_run)

    try:
        media.extract_audio(video, tmp_path)
    except RuntimeError as exc:
        assert "ffmpeg failed to extract audio" in str(exc)
        assert "Invalid data" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
