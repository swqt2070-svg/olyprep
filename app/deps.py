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


# Извлекаем токен из куки
def get_current_user(request: Request, db=Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
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
