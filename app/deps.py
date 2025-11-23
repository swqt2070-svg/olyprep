from fastapi import Depends, HTTPException, Request
from app.database import SessionLocal
from app.models import User
from app.security import SECRET_KEY
import jwt


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db=Depends(get_db)) -> User:
    """
    Достаём JWT токен из куки access_token и по нему загружаем пользователя.
    """
    token = request.cookies.get("access_token", "")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.get(User, data.get("id"))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


def require_role(*roles: str):
    """
    Универсальная проверка ролей.
    Пример:
      user: User = Depends(require_role("admin", "teacher"))
    """
    def dependency(user: User = Depends(get_current_user)):
        if roles and user.role not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return dependency


def require_teacher_or_admin(user: User = Depends(get_current_user)):
    """
    Шорткат: разрешаем только admin и teacher.
    """
    if user.role not in ("admin", "teacher"):
        raise HTTPException(status_code=403, detail="Forbidden")
    return user
