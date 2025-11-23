from typing import Callable, Generator, Optional

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal
from app.security import decode_token


# ---------- БАЗА ДАННЫХ ----------


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- ОБЩАЯ ЛОГИКА ТОКЕНА ----------


def _load_user_from_token(
    token: str,
    db: Session,
) -> Optional[models.User]:
    """
    Декодирует токен и вытаскивает пользователя.
    Возвращает None, если токен/пользователь некорректен.
    """
    try:
        data = decode_token(token)
    except Exception:
        return None

    user_id = data.get("id")
    if not user_id:
        return None

    user = db.get(models.User, user_id)
    return user


# ---------- ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ ----------


def get_current_user(
    access_token: Optional[str] = Cookie(default=None, alias="access_token"),
    db: Session = Depends(get_db),
) -> models.User:
    """
    Обязательная авторизация. 401, если токена нет / битый / юзер не найден.
    """
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    user = _load_user_from_token(access_token, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return user


def get_current_user_optional(
    access_token: Optional[str] = Cookie(default=None, alias="access_token"),
    db: Session = Depends(get_db),
) -> Optional[models.User]:
    """
    Необязательный юзер. Если нет токена или он битый — возвращает None.
    Удобно для UI, чтобы показывать/прятать элементы.
    """
    if not access_token:
        return None

    user = _load_user_from_token(access_token, db)
    return user


# ---------- ГОТОВЫЕ ГАРДЫ ПО РОЛЯМ ----------


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


def require_teacher(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != "teacher":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher access required",
        )
    return user


def require_teacher_or_admin(
    user: models.User = Depends(get_current_user),
) -> models.User:
    if user.role not in ("teacher", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher or admin access required",
        )
    return user


def require_student(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Student access required",
        )
    return user


# ---------- ГИБКИЙ ГАРД ----------


def require_role(*roles: str) -> Callable[[models.User], models.User]:
    """
    Универсальный гард по ролям.

    Пример:

        @router.get("/admin/users")
        def list_users(
            db: Session = Depends(get_db),
            current_user: User = Depends(require_role("admin")),
        ):
            ...

        Depends(require_role("admin", "teacher"))
    """

    def dependency(user: models.User = Depends(get_current_user)) -> models.User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access requires one of roles: {', '.join(roles)}",
            )
        return user

    return dependency
