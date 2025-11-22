from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app import models

from app.routers import auth, users, questions, tests

app = FastAPI(title="OlyPrep MVP")

# создаём таблицы
Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(questions.router)
app.include_router(tests.router)

@app.get("/")
def root():
    return {"status": "ok", "version": "2"}

