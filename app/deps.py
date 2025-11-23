from __future__ import annotations

from typing import Generator, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import User
from app.security import decode_token


# ---------- DB SESSION ----------


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- AUTH HELPERS ----------


def _extract_token(request: Request) -> Optional[str]:
    """
    Достаём access_token:
    1) из cookie `access_token`
    2) если нет — из Authorization: Bearer <token>
    """
    token = request.cookies.get("access_token")
    if token:
        return token

    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не авторизован",
        )

    try:
        data = decode_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный токен",
        )

    user_id = data.get("id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный токен (нет id пользователя)",
        )

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не найден",
        )

    return user


# ---------- ROLE GUARDS ----------


def require_active_user(user: User = Depends(get_current_user)) -> User:
    """
    Просто требует валидного залогиненного пользователя.
    Используем там, где не важна конкретная роль.
    """
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуется роль admin",
        )
    return user


def require_teacher_or_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("teacher", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуется роль teacher или admin",
        )
    return user


def require_student(user: User = Depends(get_current_user)) -> User:
    """
    Гард для учеников (для прохождения тестов и т.п.).
    Именно этой функции не хватало, из‑за чего падал импорт.
    """
    if user.role != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуется роль student",
        )
    return user
