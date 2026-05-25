from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import User, UserRole
from app.schemas import AuthRequest, AuthResponse, UserPublic
from app.security import create_access_token, get_password_hash, verify_password


router = APIRouter(prefix="/auth", tags=["auth"])


def make_auth_response(user: User) -> AuthResponse:
    token = create_access_token(user.id, {"role": user.role.value})
    return AuthResponse(access_token=token, user=UserPublic.model_validate(user))


@router.post("/register", response_model=AuthResponse)
def register(payload: AuthRequest, db: Session = Depends(get_db)) -> AuthResponse:
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user_count = db.scalar(select(func.count()).select_from(User)) or 0
    role = UserRole.admin if user_count == 0 else UserRole.user
    user = User(email=payload.email.lower(), hashed_password=get_password_hash(payload.password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return make_auth_response(user)


@router.post("/login", response_model=AuthResponse)
def login(payload: AuthRequest, db: Session = Depends(get_db)) -> AuthResponse:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled")
    return make_auth_response(user)


@router.get("/me", response_model=UserPublic)
def me(user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.model_validate(user)

