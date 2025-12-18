from fastapi import APIRouter, Depends, HTTPException, Form, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.models import User
from app.security import hash_password, verify_password, create_token
from app.deps import get_db, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
def register(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    # если пользователь с таким логином уже есть
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "login exists")

    # если это самый первый пользователь в системе — делаем его admin
    total_users = db.query(User).count()
    role = "admin" if total_users == 0 else "student"

    user = User(email=email, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    return {"ok": True, "role": role}


@router.post("/login")
def login(
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Invalid login or password")

    token = create_token({"id": user.id, "role": user.role})
    response.set_cookie("access_token", token, httponly=True)
    return {"ok": True, "role": user.role}


@router.post("/token")
def issue_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    OAuth2-compatible token endpoint for Swagger/clients.
    Accepts username/password, returns bearer token.
    """
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Invalid credentials")

    token = create_token({"id": user.id, "role": user.role})
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "role": user.role}
