from typing import Generator, Optional, Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import User
from app.security import verify_token


# ---------- DB SESSION ----------


def get_db() -> Generator[Session, None, None]:
    """Отдаёт сессию БД в зависимость и корректно её закрывает."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- AUTH / CURRENT USER ----------


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """
    Достаёт JWT из cookie `access_token`, декодирует его через verify_token
    и возвращает текущего пользователя, иначе 401.
    """
    token: Optional[str] = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    data = verify_token(token)  # verify_token должен вернуть payload (dict) с id

    user_id = data.get("id")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user


# ---------- ROLE HELPERS ----------


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Доступ только для admin."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


def require_teacher(user: User = Depends(get_current_user)) -> User:
    """Доступ для teacher и admin."""
    if user.role not in ("teacher", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher access required",
        )
    return user


def require_teacher_or_admin(user: User = Depends(get_current_user)) -> User:
    """Общая проверка для teacher или admin."""
    if user.role not in ("teacher", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher or admin access required",
        )
    return user


def require_student(user: User = Depends(get_current_user)) -> User:
    """
    Доступ только для student (и, по желанию, admin — чтобы ты мог тестить).
    Если хочешь, чтобы админ мог проходить тесты как студент — оставь admin тут.
    Если нет — измени условие на user.role != "student".
    """
    if user.role not in ("student", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Student access required",
        )
    return user


def require_role(required_role: str) -> Callable[[User], User]:
    """
    Фабрика зависимости по роли.

    Использование в роутере обычно такое:
        @router.get("/admin-only")
        def some_view(current_user: User = Depends(require_role("admin"))):
            ...

    То есть require_role("admin") возвращает функцию-Depends, которая уже
    проверяет роль и отдаёт current_user.
    """

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{required_role} access required",
            )
        return user

    return dependency
