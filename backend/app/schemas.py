from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import JobStatus, SourceType, UserRole


class UserPublic(BaseModel):
    id: str
    email: EmailStr
    role: UserRole
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuthRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class JobPublic(BaseModel):
    id: str
    source_type: SourceType
    source_url: str | None = None
    input_filename: str | None = None
    status: JobStatus
    progress: int
    title: str | None = None
    language: str | None = None
    duration: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    completed_at: datetime | None = None
    metadata_json: dict = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class JobCreateResponse(BaseModel):
    job: JobPublic
    guest_token: str | None = None


class JobListResponse(BaseModel):
    jobs: list[JobPublic]


class MarkdownResponse(BaseModel):
    markdown: str


class AdminStats(BaseModel):
    users: int
    jobs: int
    pending: int
    processing: int
    failed: int
    storage_bytes: int

