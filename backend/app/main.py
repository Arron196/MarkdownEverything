from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, create_all
from app.models import User, UserRole
from app.routers import admin, auth, jobs
from app.security import get_password_hash
from app.system import get_capabilities


app = FastAPI(title=settings.app_name, docs_url="/api/docs", openapi_url="/api/openapi.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_origin,
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    create_all()
    bootstrap_admin()


def bootstrap_admin() -> None:
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == settings.bootstrap_admin_email.lower()))
        if existing:
            return
        user = User(
            email=settings.bootstrap_admin_email.lower(),
            hashed_password=get_password_hash(settings.bootstrap_admin_password),
            role=UserRole.admin,
        )
        db.add(user)
        db.commit()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "capabilities": get_capabilities()}


app.include_router(auth.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
