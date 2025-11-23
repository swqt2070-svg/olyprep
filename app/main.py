from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app import models
from app.routers import auth, users, questions, tests, ui, tests_new  # ← ДОБАВЛЕН ui


app = FastAPI(title="OlyPrep MVP")

Base.metadata.create_all(bind=engine)

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
app.include_router(tests.router)
app.include_router(ui.router)  # ← ДОБАВЛЕНО

@app.get("/")
def root():
    return {"status": "ok", "version": "3.1 UI"}
