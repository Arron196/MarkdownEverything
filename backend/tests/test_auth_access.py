from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.auth import assert_job_access
from app.models import Job, SourceType, User, UserRole
from app.security import create_guest_token, hash_secret


def make_job(token: str) -> Job:
    return Job(
        id="job-1",
        source_type=SourceType.text,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        guest_token_hash=hash_secret(token),
    )


def test_guest_token_allows_access():
    token = create_guest_token()
    assert_job_access(make_job(token), None, token)


def test_wrong_guest_token_denies_access():
    token = create_guest_token()
    with pytest.raises(HTTPException):
        assert_job_access(make_job(token), None, "wrong")


def test_admin_allows_access():
    token = create_guest_token()
    user = User(id="u1", email="admin@example.com", hashed_password="x", role=UserRole.admin)
    assert_job_access(make_job(token), user, None)

