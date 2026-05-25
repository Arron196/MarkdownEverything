from collections.abc import Generator

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Job, User, UserRole
from app.security import decode_access_token, hash_secret


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_current_user_optional(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        return None
    user = db.get(User, payload["sub"])
    if not user or not user.is_active:
        return None
    return user


def get_current_user(user: User | None = Depends(get_current_user_optional)) -> User:
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


def get_guest_token(
    guest_token_query: str | None = Query(default=None, alias="guest_token"),
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
) -> str | None:
    return guest_token_query or x_guest_token


def assert_job_access(job: Job, user: User | None, guest_token: str | None) -> None:
    if job.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if user and (user.role == UserRole.admin or job.owner_user_id == user.id):
        return
    if guest_token and job.guest_token_hash and hash_secret(guest_token) == job.guest_token_hash:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this job")


def get_accessible_job(
    job_id: str,
    user: User | None = Depends(get_current_user_optional),
    guest_token: str | None = Depends(get_guest_token),
    db: Session = Depends(get_db),
) -> Generator[Job, None, None]:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    assert_job_access(job, user, guest_token)
    yield job

