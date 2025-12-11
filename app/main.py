from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import jwt

from app.database import Base, engine, init_db
from app.config import settings
from app import models
from app.routers import auth, users, questions, ui, tests_new  # ← ДОБАВЛЕН ui


app = FastAPI(title="OlyPrep MVP")
templates = Jinja2Templates(directory="app/templates")

# Создаём/мигрируем БД (добавляет options/correct при необходимости)
init_db()

# static files (for uploads)
static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
uploads_dir = static_dir / "uploads"
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(tests_new.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(questions.router)
app.include_router(ui.router)  # ← ДОБАВЛЕНО

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    user = None
    token = request.cookies.get("access_token")
    if token:
        try:
            data = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            user_id = data.get("id") or data.get("sub")
            if user_id:
                from app.database import SessionLocal
                from app.models import User

                db = SessionLocal()
                try:
                    user = db.get(User, int(user_id))
                except Exception:
                    user = None
                finally:
                    db.close()
        except Exception:
            pass

    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "user": user,
            "error": error,
        },
    )



@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    if exc.status_code in (401, 403):
        msg = exc.detail or "???????????? ????"
        return RedirectResponse(url=f"/?error={msg}", status_code=303)
    return RedirectResponse(url=f"/?error={exc.detail}", status_code=303)
