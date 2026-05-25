from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_admin_user
from app.db import get_db
from app.models import Job, JobLog, JobStatus, User
from app.schemas import AdminStats, JobPublic, UserPublic
from app.storage import storage_usage_bytes


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_admin_user)])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "checked_at": datetime.now(timezone.utc).isoformat()}


@router.get("/stats", response_model=AdminStats)
def stats(db: Session = Depends(get_db)) -> AdminStats:
    users = db.scalar(select(func.count()).select_from(User)) or 0
    jobs = db.scalar(select(func.count()).select_from(Job).where(Job.deleted_at.is_(None))) or 0
    pending = db.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.pending)) or 0
    processing = db.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.processing)) or 0
    failed = db.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.failed)) or 0
    return AdminStats(
        users=users,
        jobs=jobs,
        pending=pending,
        processing=processing,
        failed=failed,
        storage_bytes=storage_usage_bytes(),
    )


@router.get("/users", response_model=list[UserPublic])
def users(db: Session = Depends(get_db)) -> list[UserPublic]:
    rows = db.scalars(select(User).order_by(User.created_at.desc()).limit(200)).all()
    return [UserPublic.model_validate(user) for user in rows]


@router.get("/jobs", response_model=list[JobPublic])
def jobs(db: Session = Depends(get_db)) -> list[JobPublic]:
    rows = db.scalars(select(Job).where(Job.deleted_at.is_(None)).order_by(Job.created_at.desc()).limit(200)).all()
    return [JobPublic.model_validate(job) for job in rows]


@router.get("/logs")
def logs(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(JobLog).order_by(JobLog.created_at.desc()).limit(200)).all()
    return [
        {
            "id": row.id,
            "job_id": row.job_id,
            "level": row.level,
            "message": row.message,
            "details": row.details,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/workers")
def workers() -> dict:
    from app.celery_app import celery_app

    inspector = celery_app.control.inspect(timeout=1)
    return {
        "active": inspector.active() or {},
        "reserved": inspector.reserved() or {},
        "stats": inspector.stats() or {},
    }

