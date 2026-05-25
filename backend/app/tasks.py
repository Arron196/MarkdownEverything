import asyncio
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.celery_app import celery_app
from app.converters.documents import convert_by_extension, convert_html, convert_text
from app.converters.media import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, convert_audio, convert_video_file, convert_video_url
from app.converters.web import convert_webpage
from app.db import SessionLocal
from app.markdown import render_document_markdown, render_media_markdown
from app.models import Job, JobLog, JobStatus, SourceType
from app.services.ai import generate_summary
from app.storage import append_job_log_file, ensure_job_dirs, remove_job_dir, write_result


def run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def enqueue_job(job_id: str, queue_name: str) -> None:
    from app.config import settings

    if settings.sync_conversions:
        process_job(job_id)
    else:
        process_job.apply_async(args=[job_id], queue=queue_name)


def log(db, job: Job, level: str, message: str, details: dict | None = None) -> None:
    db.add(JobLog(job_id=job.id, level=level, message=message, details=details or {}))
    append_job_log_file(job.id, f"[{level.upper()}] {message}")
    db.commit()


def set_progress(db, job: Job, progress: int, message: str | None = None) -> None:
    job.progress = progress
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    if message:
        log(db, job, "info", message)


@celery_app.task(name="app.tasks.process_job")
def process_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.deleted_at is not None:
            return
        try:
            job.status = JobStatus.processing
            job.progress = 5
            job.started_at = datetime.now(timezone.utc)
            db.commit()
            log(db, job, "info", "Conversion started")
            paths = ensure_job_dirs(job.id)
            result = run_conversion(job, paths)
            set_progress(db, job, 70, "Content extracted")

            summary = run_async(generate_summary(result.summary_seed or result.body, result.title))
            if result.timeline:
                markdown = render_media_markdown(
                    title=result.title,
                    source_type=result.source_type,
                    timeline=result.timeline,
                    summary=summary,
                    source_url=result.source_url,
                    duration=result.duration,
                    language=result.language,
                )
            else:
                markdown = render_document_markdown(
                    title=result.title,
                    source_type=result.source_type,
                    body=result.body,
                    summary=summary,
                    source_url=result.source_url,
                    author=result.author,
                    created_at=result.created_at,
                    resources=result.resources,
                )
            set_progress(db, job, 85, "Markdown rendered")
            metadata = {
                "title": result.title,
                "source_type": result.source_type,
                "source_url": result.source_url,
                "language": result.language,
                "duration": result.duration,
                **result.metadata,
            }
            md_path, zip_path = write_result(job.id, markdown, metadata)
            job.title = result.title
            job.language = result.language
            job.duration = result.duration
            job.markdown_path = str(md_path)
            job.zip_path = str(zip_path)
            job.assets_dir = str(paths["assets"])
            job.metadata_json = metadata
            job.status = JobStatus.succeeded
            job.progress = 100
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            log(db, job, "info", "Conversion succeeded")
        except Exception as exc:
            job.status = JobStatus.failed
            job.progress = max(job.progress, 1)
            job.error_message = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            log(db, job, "error", str(exc), {"traceback": traceback.format_exc()})


def run_conversion(job: Job, paths: dict[str, Path]):
    input_dir = paths["input"]
    assets_dir = paths["assets"]
    work_dir = paths["root"] / "work"
    work_dir.mkdir(exist_ok=True)

    if job.source_type == SourceType.webpage:
        if not job.source_url:
            raise ValueError("source_url is required for webpage jobs")
        return run_async(convert_webpage(job.source_url, assets_dir))
    if job.source_type == SourceType.video_url:
        if not job.source_url:
            raise ValueError("source_url is required for video URL jobs")
        return convert_video_url(job.source_url, work_dir)

    path = next((p for p in input_dir.iterdir() if p.is_file()), None)
    if not path:
        raise ValueError("Input file is missing")

    if job.source_type == SourceType.text:
        return convert_text(path)
    if job.source_type == SourceType.html:
        return convert_html(path)
    if job.source_type in {SourceType.csv, SourceType.pdf, SourceType.docx}:
        return convert_by_extension(path, assets_dir)
    if job.source_type == SourceType.audio or path.suffix.lower() in AUDIO_EXTENSIONS:
        return convert_audio(path)
    if job.source_type == SourceType.video or path.suffix.lower() in VIDEO_EXTENSIONS:
        return convert_video_file(path, work_dir)
    raise ValueError(f"Unsupported source type: {job.source_type}")


@celery_app.task(name="app.tasks.cleanup_expired_jobs")
def cleanup_expired_jobs() -> int:
    removed = 0
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        jobs = db.scalars(
            select(Job).where(Job.deleted_at.is_(None), Job.expires_at < now)
        ).all()
        for job in jobs:
            job.status = JobStatus.expired
            job.deleted_at = now
            remove_job_dir(job.id)
            removed += 1
        db.commit()
    return removed
