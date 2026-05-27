from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import get_accessible_job, get_current_user_optional
from app.config import settings
from app.db import get_db
from app.models import Job, JobStatus, SourceType, User
from app.schemas import JobCreateResponse, JobListResponse, JobPublic, MarkdownResponse
from app.security import create_guest_token, hash_secret
from app.services.url_security import assert_public_url
from app.storage import remove_job_dir, save_upload, write_text_input
from app.tasks import enqueue_job


router = APIRouter(prefix="/jobs", tags=["jobs"])

ALLOWED_EXTENSIONS: dict[str, SourceType] = {
    ".txt": SourceType.text,
    ".md": SourceType.text,
    ".html": SourceType.html,
    ".htm": SourceType.html,
    ".csv": SourceType.csv,
    ".pdf": SourceType.pdf,
    ".docx": SourceType.docx,
    ".mp3": SourceType.audio,
    ".wav": SourceType.audio,
    ".m4a": SourceType.audio,
    ".aac": SourceType.audio,
    ".ogg": SourceType.audio,
    ".flac": SourceType.audio,
    ".mp4": SourceType.video,
    ".mov": SourceType.video,
    ".mkv": SourceType.video,
    ".webm": SourceType.video,
}

ALLOWED_MIME_PREFIXES = {"text/", "audio/", "video/"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/csv",
    "application/octet-stream",
}


def queue_for_source(source_type: SourceType) -> str:
    if source_type == SourceType.webpage:
        return "web"
    if source_type in {SourceType.audio}:
        return "audio"
    if source_type in {SourceType.video, SourceType.video_url}:
        return "video"
    return "file"


def detect_source_type(filename: str | None, requested: str | None, url: str | None, text: str | None, html: str | None) -> SourceType:
    if requested:
        try:
            return SourceType(requested)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported source_type") from exc
    if filename:
        extension = Path(filename).suffix.lower()
        if extension in ALLOWED_EXTENSIONS:
            return ALLOWED_EXTENSIONS[extension]
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported file type: {extension}")
    if html:
        return SourceType.html
    if text:
        return SourceType.text
    if url:
        return SourceType.webpage
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide a URL, text, HTML, or file")


def ensure_allowed_upload(upload: UploadFile) -> None:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported file type: {extension}")
    content_type = upload.content_type or "application/octet-stream"
    if content_type in ALLOWED_MIME_TYPES or any(content_type.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES):
        return
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported content type: {content_type}")


def requester_hash(request: Request) -> str:
    client = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    return hash_secret(f"{client}:{ua}")


def ensure_concurrency_allowed(db: Session, user: User | None, requester: str) -> None:
    active = [JobStatus.pending, JobStatus.processing]
    if user:
        count = db.scalar(
            select(func.count()).select_from(Job).where(Job.owner_user_id == user.id, Job.status.in_(active), Job.deleted_at.is_(None))
        )
    else:
        count = db.scalar(
            select(func.count()).select_from(Job).where(Job.requester_hash == requester, Job.status.in_(active), Job.deleted_at.is_(None))
        )
    limit = settings.user_concurrency_limit if user else settings.guest_concurrency_limit
    if (count or 0) >= limit:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many active conversion jobs")


@router.post("", response_model=JobCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    request: Request,
    source_type: str | None = Form(default=None),
    url: str | None = Form(default=None),
    text: str | None = Form(default=None),
    html: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> JobCreateResponse:
    requester = requester_hash(request)
    ensure_concurrency_allowed(db, user, requester)

    detected_type = detect_source_type(file.filename if file else None, source_type, url, text, html)
    if url:
        assert_public_url(url)
    if file:
        ensure_allowed_upload(file)
    guest_token = None if user else create_guest_token()
    expires_at = datetime.now(timezone.utc) + (
        timedelta(days=settings.user_retention_days) if user else timedelta(hours=settings.guest_retention_hours)
    )
    job = Job(
        owner_user_id=user.id if user else None,
        guest_token_hash=hash_secret(guest_token) if guest_token else None,
        requester_hash=requester,
        source_type=detected_type,
        source_url=url,
        input_filename=file.filename if file else None,
        input_mime=file.content_type if file else None,
        queue_name=queue_for_source(detected_type),
        expires_at=expires_at,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    input_size: int | None = None
    if file:
        path, input_size = await save_upload(job.id, file)
        job.input_filename = path.name
    elif text is not None:
        path = write_text_input(job.id, "input.txt", text)
        job.input_filename = path.name
        input_size = len(text.encode("utf-8"))
    elif html is not None:
        path = write_text_input(job.id, "input.html", html)
        job.input_filename = path.name
        input_size = len(html.encode("utf-8"))
    job.input_size = input_size
    db.commit()
    db.refresh(job)

    try:
        enqueue_job(job.id, job.queue_name)
    except Exception as exc:
        job.status = JobStatus.failed
        job.error_message = "Failed to enqueue conversion job"
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=job.error_message) from exc
    db.refresh(job)
    return JobCreateResponse(job=JobPublic.model_validate(job), guest_token=guest_token)


@router.get("", response_model=JobListResponse)
def list_jobs(
    type: SourceType | None = None,
    status_filter: JobStatus | None = None,
    search: str | None = None,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> JobListResponse:
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required for history")
    query = select(Job).where(Job.owner_user_id == user.id, Job.deleted_at.is_(None)).order_by(Job.created_at.desc())
    if type:
        query = query.where(Job.source_type == type)
    if status_filter:
        query = query.where(Job.status == status_filter)
    if search:
        like = f"%{search}%"
        query = query.where(or_(Job.title.ilike(like), Job.source_url.ilike(like), Job.input_filename.ilike(like)))
    jobs = db.scalars(query.limit(100)).all()
    return JobListResponse(jobs=[JobPublic.model_validate(job) for job in jobs])


@router.get("/{job_id}", response_model=JobPublic)
def get_job(job: Job = Depends(get_accessible_job)) -> JobPublic:
    return JobPublic.model_validate(job)


@router.get("/{job_id}/markdown", response_model=MarkdownResponse)
def get_markdown(job: Job = Depends(get_accessible_job)) -> MarkdownResponse:
    if job.status != JobStatus.succeeded or not job.markdown_path:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Markdown is not available yet")
    path = Path(job.markdown_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Markdown file missing")
    return MarkdownResponse(markdown=path.read_text(encoding="utf-8"))


@router.get("/{job_id}/assets/{asset_path:path}")
def get_job_asset(asset_path: str, job: Job = Depends(get_accessible_job)) -> FileResponse:
    if job.status != JobStatus.succeeded or not job.assets_dir:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Assets are not available yet")
    assets_dir = Path(job.assets_dir).resolve()
    path = (assets_dir / asset_path).resolve()
    try:
        path.relative_to(assets_dir)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid asset path") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset file missing")
    return FileResponse(path)


@router.get("/{job_id}/download")
def download_job(format: str = "zip", job: Job = Depends(get_accessible_job)) -> FileResponse:
    if job.status != JobStatus.succeeded:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Result is not available yet")
    path = Path(job.markdown_path if format == "md" else job.zip_path or "")
    if format not in {"md", "zip"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="format must be md or zip")
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result file missing")
    media_type = "text/markdown" if format == "md" else "application/zip"
    filename = f"{job.title or job.id}.{format if format == 'zip' else 'md'}"
    return FileResponse(path, media_type=media_type, filename=filename)


@router.post("/{job_id}/retry", response_model=JobPublic, status_code=status.HTTP_202_ACCEPTED)
def retry_job(job: Job = Depends(get_accessible_job), db: Session = Depends(get_db)) -> JobPublic:
    if job.status not in {JobStatus.failed, JobStatus.expired, JobStatus.succeeded}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only finished jobs can be retried")
    if job.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    job.status = JobStatus.pending
    job.progress = 0
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.markdown_path = None
    job.zip_path = None
    job.metadata_json = {}
    job.expires_at = datetime.now(timezone.utc) + (
        timedelta(days=settings.user_retention_days) if job.owner_user_id else timedelta(hours=settings.guest_retention_hours)
    )
    db.commit()
    try:
        enqueue_job(job.id, job.queue_name)
    except Exception as exc:
        job.status = JobStatus.failed
        job.error_message = "Failed to enqueue conversion job"
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=job.error_message) from exc
    db.refresh(job)
    return JobPublic.model_validate(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job: Job = Depends(get_accessible_job), db: Session = Depends(get_db)) -> None:
    job.deleted_at = datetime.now(timezone.utc)
    db.commit()
    remove_job_dir(job.id)
