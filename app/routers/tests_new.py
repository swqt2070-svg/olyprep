from __future__ import annotations

import json
from typing import Dict, Any, List

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import require_student
from app.models import Test, TestQuestion, Question, Answer

router = APIRouter(prefix="/ui/tests", tags=["Tests UI wizard"])
templates = Jinja2Templates(directory="app/templates")


def _load_test_questions(db: Session, test_id: int) -> tuple[Test, List[TestQuestion]]:
    """Загрузить тест и связанные вопросы с вариантами."""
    test = db.query(Test).filter(Test.id == test_id).first()
    if not test:
        raise HTTPException(status_code=404, detail="Тест не найден")

    test_questions = (
        db.query(TestQuestion)
        .options(joinedload(TestQuestion.question).joinedload(Question.answers))
        .filter(TestQuestion.test_id == test_id)
        .order_by(TestQuestion.order)
        .all()
    )

    if not test_questions:
        raise HTTPException(status_code=404, detail="В тесте нет задач")

    return test, test_questions


def _build_results(
    request: Request,
    test: Test,
    test_questions: List[TestQuestion],
    state: Dict[str, Any],
) -> HTMLResponse:
    """Посчитать баллы и отрисовать страницу результатов (без записи в БД)."""
    answers_state: Dict[str, Any] = state.get("answers", {})
    rows = []
    total_score = 0.0
    max_total = 0.0

    for idx, tq in enumerate(test_questions):
        q: Question = tq.question
        q_key = str(q.id)
        stored = answers_state.get(q_key)

        max_points = getattr(tq, "max_points", 1) or 1
        max_total += max_points

        answer_type = getattr(q, "answer_type", "text")
        all_answers: List[Answer] = list(q.answers or [])

        your_answer = ""
        correct_answer = ""
        gained = 0.0

        if answer_type == "text":
            correct_answer = (
                getattr(q, "correct_answer_text", None)
                or getattr(q, "correct_text", None)
                or ""
            )
            if stored and stored.get("mode") == "text":
                your_answer = stored.get("value") or ""
                if correct_answer:
                    if (
                        your_answer.strip().lower()
                        == correct_answer.strip().lower()
                    ):
                        gained = max_points
        else:
            # один/несколько вариантов
            correct_opts = [a for a in all_answers if getattr(a, "is_correct", False)]
            correct_ids = [a.id for a in correct_opts]
            correct_answer = "; ".join(a.text for a in correct_opts if a.text)

            if stored:
                mode = stored.get("mode")
                value = stored.get("value") or []
                if mode == "single":
                    selected_ids = [int(value)]
                elif mode == "multiple":
                    selected_ids = [int(v) for v in value]
                else:
                    selected_ids = []

                your_answer = "; ".join(
                    a.text for a in all_answers if a.id in selected_ids and a.text
                )

                if set(selected_ids) == set(correct_ids) and correct_ids:
                    gained = max_points

        total_score += gained

        rows.append(
            {
                "index": idx + 1,
                "question": q,
                "your_answer": your_answer or "—",
                "correct_answer": correct_answer or "—",
                "score": gained,
                "max_points": max_points,
            }
        )

    ctx = {
        "request": request,
        "test": test,
        "rows": rows,
        "total_score": total_score,
        "max_total": max_total,
    }
    return templates.TemplateResponse("test_result.html", ctx)


@router.get("/run/{test_id}/{index}", response_class=HTMLResponse)
async def run_test_get(
    test_id: int,
    index: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_student),
):
    """
    Первый показ страницы или прямой переход по ссылке /ui/tests/run/{test_id}/{index}
    index — 0‑based номер вопроса.
    """
    test, test_questions = _load_test_questions(db, test_id)

    total_questions = len(test_questions)
    if index < 0:
        index = 0
    if index >= total_questions:
        index = total_questions - 1

    tq = test_questions[index]
    question: Question = tq.question

    # пустое состояние новой попытки
    state = {"current_index": index, "answers": {}}
    state_json = json.dumps(state, ensure_ascii=False)

    # варианты без пустых текстов
    options = [a for a in (question.answers or []) if (a.text or "").strip()]

    ctx = {
        "request": request,
        "test": test,
        "question": question,
        "answers": options,
        "index": index,
        "total_questions": total_questions,
        "max_points": getattr(tq, "max_points", 1) or 1,
        "selected_answer_id": None,
        "selected_answer_ids": [],
        "answer_text": "",
        "state_json": state_json,
    }
    return templates.TemplateResponse("test_run.html", ctx)


@router.post("/run/{test_id}/{index}", response_class=HTMLResponse)
async def run_test_post(
    test_id: int,
    index: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_student),
):
    """
    Обработка ответов + навигация (предыдущий/следующий/к вопросу N/завершить).
    Всё состояние теста хранится в скрытом JSON‑поле state.
    """
    form = await request.form()
    action = form.get("action", "stay")

    # актуальный индекс — из формы, а не из URL
    try:
        index = int(form.get("index", "0"))
    except ValueError:
        index = 0

    raw_state = form.get("state") or "{}"
    try:
        state: Dict[str, Any] = json.loads(raw_state)
    except json.JSONDecodeError:
        state = {}
    if "answers" not in state:
        state["answers"] = {}
    answers_state: Dict[str, Any] = state["answers"]

    test, test_questions = _load_test_questions(db, test_id)
    total_questions = len(test_questions)

    if index < 0:
        index = 0
    if index >= total_questions:
        index = total_questions - 1

    tq = test_questions[index]
    question: Question = tq.question
    q_key = str(question.id)

    # читаем ответ из формы
    answer_text = (form.get("answer_text") or "").strip()
    selected_single = form.get("selected_answer_id")
    selected_multi = [v for v in form.getlist("selected_answer_ids") if v]

    if answer_text:
        answers_state[q_key] = {"mode": "text", "value": answer_text}
    elif selected_multi:
        answers_state[q_key] = {
            "mode": "multiple",
            "value": [int(v) for v in selected_multi],
        }
    elif selected_single:
        answers_state[q_key] = {"mode": "single", "value": int(selected_single)}
    else:
        answers_state.pop(q_key, None)

    # навигация
    if action == "prev":
        new_index = max(index - 1, 0)
    elif action == "next":
        new_index = min(index + 1, total_questions - 1)
    elif action.startswith("goto:"):
        try:
            new_index = int(action.split(":", 1)[1])
        except ValueError:
            new_index = index
        new_index = max(0, min(new_index, total_questions - 1))
    elif action == "finish":
        state["current_index"] = index
        return _build_results(request, test, test_questions, state)
    else:
        new_index = index

    state["current_index"] = new_index
    state_json = json.dumps(state, ensure_ascii=False)

    tq = test_questions[new_index]
    question = tq.question
    max_points = getattr(tq, "max_points", 1) or 1

    # восстановление ответа для нового вопроса
    q_key = str(question.id)
    stored = answers_state.get(q_key, {})
    selected_answer_id = None
    selected_answer_ids: List[int] = []
    answer_text_restored = ""

    mode = stored.get("mode")
    value = stored.get("value")
    if mode == "text":
        answer_text_restored = value or ""
    elif mode == "single":
        if value is not None:
            selected_answer_id = int(value)
    elif mode == "multiple":
        selected_answer_ids = [int(v) for v in (value or [])]

    options = [a for a in (question.answers or []) if (a.text or "").strip()]

    ctx = {
        "request": request,
        "test": test,
        "question": question,
        "answers": options,
        "index": new_index,
        "total_questions": total_questions,
        "max_points": max_points,
        "selected_answer_id": selected_answer_id,
        "selected_answer_ids": selected_answer_ids,
        "answer_text": answer_text_restored,
        "state_json": state_json,
    }
    return templates.TemplateResponse("test_run.html", ctx)
