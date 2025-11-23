from typing import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import User
from app.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


def get_db() -> Generator[Session, None, None]:
    """
    Зависимость для получения сессии БД.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Достаёт текущего пользователя из JWT‑токена.

    Ожидается, что в токене есть поле "id" (ID пользователя в БД).
    """
    data = decode_access_token(token)
    if not data or "id" not in data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не удалось авторизоваться",
        )

    user = db.get(User, data["id"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не найден",
        )

    return user


def require_role(*allowed_roles: str):
    """
    Фабрика зависимостей для проверки роли.

    Пример:
        @router.get("/admin-only")
        def admin_only(user: User = Depends(require_role("admin"))):
            ...
    """

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав",
            )
        return user

    return dependency


def require_student(user: User = Depends(require_role("student"))) -> User:
    """
    Разрешён только студент.
    """
    return user


def require_teacher_or_admin(
    user: User = Depends(require_role("teacher", "admin")),
) -> User:
    """
    Разрешены teacher и admin.
    """
    return user
