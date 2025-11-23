# app/routers/tests_new.py
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    Request,
    HTTPException,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.deps import (
    get_db,
    get_current_user,
    require_teacher_or_admin,
    require_student,
)
from app.models import Question, Answer, User
from app.quiz_models import (
    QuizTest,
    QuizTestQuestion,
    QuizSubmission,
    QuizSubmissionAnswer,
)

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(
    prefix="/ui/tests",
    include_in_schema=False,  # только UI, без OpenAPI
)


# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------

def _normalize(s: str) -> str:
    """Убираем пробелы и приводим к нижнему регистру."""
    return "".join((s or "").lower().split())


def _get_max_score(test: QuizTest) -> int:
    return sum(q.points for q in test.questions)


def _get_correct_option_ids(db: Session, question_id: int) -> List[int]:
    """
    Предполагаем, что в Answer есть поля:
      - question_id
      - is_correct (bool)
    Если модель другая — нужно будет подправить.
    """
    try:
        rows = (
            db.query(Answer)
            .filter(Answer.question_id == question_id)
            .filter(getattr(Answer, "is_correct", True) == True)  # type: ignore
            .all()
        )
    except Exception:
        return []
    return [row.id for row in rows]


def _check_answer(
    db: Session,
    question: Question,
    submission_answer: QuizSubmissionAnswer,
) -> bool:
    """
    Универсальная проверка ответа по типу задачи.
    Сейчас поддерживаем:
      - answer_type == "text"
      - answer_type == "single"
      - answer_type == "multiple"
    Остальные считаем неверными (можно доработать позже).
    """
    answer_type = getattr(question, "answer_type", submission_answer.answer_type)

    if answer_type == "text":
        correct_text: Optional[str] = getattr(question, "correct_text", None)
        if not correct_text:
            return False
        return _normalize(correct_text) == _normalize(submission_answer.answer_text or "")

    if answer_type in ("single", "one"):
        # single choice
        correct_ids = _get_correct_option_ids(db, question.id)
        if not correct_ids:
            return False
        selected = (submission_answer.selected_option_ids or "").strip()
        try:
            selected_id = int(selected) if selected else None
        except ValueError:
            return False
        return selected_id in correct_ids

    if answer_type in ("multiple", "many"):
        correct_ids = set(_get_correct_option_ids(db, question.id))
        if not correct_ids:
            return False
        selected_str = submission_answer.selected_option_ids or ""
        try:
            selected_ids = {int(x) for x in selected_str.split(",") if x.strip()}
        except ValueError:
            return False
        return selected_ids == correct_ids

    # для ещё не реализованных типов пока всегда False
    return False


def _score_submission(db: Session, submission: QuizSubmission) -> None:
    """Пересчёт баллов за попытку."""
    test: QuizTest = submission.test
    questions = (
        db.query(QuizTestQuestion)
        .filter(QuizTestQuestion.test_id == test.id)
        .order_by(QuizTestQuestion.order_index)
        .all()
    )

    total_score = 0
    max_score = _get_max_score(test)

    answers_by_q: Dict[int, QuizSubmissionAnswer] = {
        a.question_id: a for a in submission.answers
    }

    for tq in questions:
        question = db.get(Question, tq.question_id)
        if question is None:
            continue

        sa = answers_by_q.get(question.id)
        if sa is None:
            continue

        is_correct = _check_answer(db, question, sa)
        sa.is_correct = is_correct
        sa.points = tq.points if is_correct else 0
        total_score += sa.points or 0

    submission.total_score = total_score
    submission.max_score = max_score
    submission.finished_at = datetime.utcnow()
    db.commit()


# ---------- СПИСОК ТЕСТОВ ----------


@router.get("", response_class=HTMLResponse)
async def tests_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Страница /ui/tests
    - для учителя/админа: список его тестов
    - для ученика: список всех тестов
    """
    role = current_user.role

    if role in ("teacher", "admin"):
        tests = (
            db.query(QuizTest)
            .filter(QuizTest.created_by_id == current_user.id)
            .order_by(QuizTest.created_at.desc())
            .all()
        )
    else:
        tests = db.query(QuizTest).order_by(QuizTest.created_at.desc()).all()

    # Для отображения количества задач и макс. баллов
    test_info = []
    for t in tests:
        max_score = _get_max_score(t)
        test_info.append(
            {
                "test": t,
                "question_count": len(t.questions),
                "max_score": max_score,
            }
        )

    return templates.TemplateResponse(
        "tests_list.html",
        {
            "request": request,
            "user": current_user,
            "role": role,
            "test_info": test_info,
        },
    )


# ---------- СОЗДАНИЕ / РЕДАКТИРОВАНИЕ ТЕСТА ----------


@router.get("/new", response_class=HTMLResponse)
async def new_test(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_teacher_or_admin),
):
    questions = db.query(Question).order_by(Question.id).all()
    return templates.TemplateResponse(
        "test_builder.html",
        {
            "request": request,
            "user": current_user,
            "mode": "create",
            "test": None,
            "questions": questions,
            "selected": {},  # нет выбранных
        },
    )


@router.post("/new")
async def create_test(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_teacher_or_admin),
):
    form = await request.form()

    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip()
    show_correct_answers = form.get("show_correct_answers") == "on"

    if not title:
        raise HTTPException(status_code=400, detail="Название теста обязательно")

    test = QuizTest(
        title=title,
        description=description or None,
        created_by_id=current_user.id,
        show_correct_answers=show_correct_answers,
    )
    db.add(test)
    db.flush()  # получаем test.id

    selected_questions: List[QuizTestQuestion] = []

    for key, value in form.items():
        # ищем ключи вида q_<id>_include
        if not key.startswith("q_") or not key.endswith("_include"):
            continue
        try:
            q_id = int(key.split("_")[1])
        except ValueError:
            continue

        include_flag = value == "on"
        if not include_flag:
            continue

        points_raw = form.get(f"q_{q_id}_points") or "1"
        try:
            points = int(points_raw)
        except ValueError:
            points = 1
        if points < 0:
            points = 0

        selected_questions.append(
            QuizTestQuestion(
                test_id=test.id,
                question_id=q_id,
                points=points,
            )
        )

    # задаём порядок
    for idx, tq in enumerate(selected_questions):
        tq.order_index = idx
        db.add(tq)

    db.commit()

    return RedirectResponse(
        url=f"/ui/tests/{test.id}/edit",
        status_code=303,
    )


@router.get("/{test_id}/edit", response_class=HTMLResponse)
async def edit_test(
    test_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_teacher_or_admin),
):
    test = db.get(QuizTest, test_id)
    if not test or test.created_by_id != current_user.id:
        raise HTTPException(status_code=404, detail="Тест не найден")

    questions = db.query(Question).order_by(Question.id).all()
    selected: Dict[int, QuizTestQuestion] = {
        tq.question_id: tq for tq in test.questions
    }

    return templates.TemplateResponse(
        "test_builder.html",
        {
            "request": request,
            "user": current_user,
            "mode": "edit",
            "test": test,
            "questions": questions,
            "selected": selected,
        },
    )


@router.post("/{test_id}/edit")
async def update_test(
    test_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_teacher_or_admin),
):
    form = await request.form()
    test = db.get(QuizTest, test_id)
    if not test or test.created_by_id != current_user.id:
        raise HTTPException(status_code=404, detail="Тест не найден")

    test.title = (form.get("title") or "").strip()
    test.description = (form.get("description") or "").strip() or None
    test.show_correct_answers = form.get("show_correct_answers") == "on"

    # сначала удаляем все старые QuizTestQuestion
    db.query(QuizTestQuestion).filter(
        QuizTestQuestion.test_id == test.id
    ).delete()

    selected_questions: List[QuizTestQuestion] = []

    for key, value in form.items():
        if not key.startswith("q_") or not key.endswith("_include"):
            continue
        try:
            q_id = int(key.split("_")[1])
        except ValueError:
            continue

        if value != "on":
            continue

        points_raw = form.get(f"q_{q_id}_points") or "1"
        try:
            points = int(points_raw)
        except ValueError:
            points = 1
        if points < 0:
            points = 0

        selected_questions.append(
            QuizTestQuestion(
                test_id=test.id,
                question_id=q_id,
                points=points,
            )
        )

    for idx, tq in enumerate(selected_questions):
        tq.order_index = idx
        db.add(tq)

    db.commit()

    return RedirectResponse(url=f"/ui/tests", status_code=303)


# ---------- ЗАПУСК И ПРОХОЖДЕНИЕ ТЕСТА УЧЕНИКОМ ----------


@router.get("/{test_id}/start")
async def start_test(
    test_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student),
):
    test = db.get(QuizTest, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Тест не найден")

    submission = QuizSubmission(
        test_id=test.id,
        student_id=current_user.id,
        started_at=datetime.utcnow(),
        max_score=_get_max_score(test),
    )
    db.add(submission)
    db.commit()

    return RedirectResponse(
        url=f"/ui/tests/run/{submission.id}/1",
        status_code=303,
    )


@router.get("/run/{submission_id}/{order}", response_class=HTMLResponse)
async def run_test_get(
    submission_id: int,
    order: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    submission = db.get(QuizSubmission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Попытка не найдена")

    test = submission.test

    # доступ: владелец попытки или учитель/админ
    if current_user.role not in ("admin", "teacher"):
        if submission.student_id != current_user.id:
            raise HTTPException(status_code=403, detail="Нет доступа")

    all_tq = (
        db.query(QuizTestQuestion)
        .filter(QuizTestQuestion.test_id == test.id)
        .order_by(QuizTestQuestion.order_index)
        .all()
    )
    total_questions = len(all_tq)
    if total_questions == 0:
        raise HTTPException(status_code=400, detail="В тесте нет задач")

    if order < 1:
        order = 1
    if order > total_questions:
        order = total_questions

    current_tq = all_tq[order - 1]
    question = db.get(Question, current_tq.question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    # варианты ответа (если есть)
    try:
        options = (
            db.query(Answer)
            .filter(Answer.question_id == question.id)
            .order_by(Answer.id)
            .all()
        )
    except Exception:
        options = []

    existing_answer = (
        db.query(QuizSubmissionAnswer)
        .filter(
            QuizSubmissionAnswer.submission_id == submission.id,
            QuizSubmissionAnswer.question_id == question.id,
        )
        .first()
    )

    answered_ids = {a.question_id for a in submission.answers}

    return templates.TemplateResponse(
        "test_run.html",
        {
            "request": request,
            "user": current_user,
            "submission": submission,
            "test": test,
            "order": order,
            "total_questions": total_questions,
            "tq": current_tq,
            "question": question,
            "options": options,
            "existing_answer": existing_answer,
            "answered_ids": answered_ids,
        },
    )


@router.post("/run/{submission_id}/{order}")
async def run_test_post(
    submission_id: int,
    order: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    form = await request.form()
    submission = db.get(QuizSubmission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Попытка не найдена")

    test = submission.test

    if current_user.role not in ("admin", "teacher"):
        if submission.student_id != current_user.id:
            raise HTTPException(status_code=403, detail="Нет доступа")

    question_id = int(form.get("question_id"))
    answer_type = form.get("answer_type") or "text"
    action = form.get("action") or "next"

    # читаем ответ
    answer_text: Optional[str] = None
    selected_option_ids: Optional[str] = None

    if answer_type == "text":
        answer_text = (form.get("answer_text") or "").strip()
    elif answer_type in ("single", "one"):
        selected = (form.get("selected_option") or "").strip()
        selected_option_ids = selected or None
    elif answer_type in ("multiple", "many"):
        # FastAPI FormData поддерживает getlist
        selected_list = form.getlist("selected_options")
        selected_option_ids = ",".join(str(x) for x in selected_list) or None

    # сохраняем / обновляем
    existing_answer = (
        db.query(QuizSubmissionAnswer)
        .filter(
            QuizSubmissionAnswer.submission_id == submission.id,
            QuizSubmissionAnswer.question_id == question_id,
        )
        .first()
    )

    if existing_answer is None:
        existing_answer = QuizSubmissionAnswer(
            submission_id=submission.id,
            question_id=question_id,
            answer_type=answer_type,
        )
        db.add(existing_answer)

    existing_answer.answer_type = answer_type
    existing_answer.answer_text = answer_text
    existing_answer.selected_option_ids = selected_option_ids

    db.commit()

    # навигация
    if action == "prev":
        target_order = max(order - 1, 1)
        return RedirectResponse(
            url=f"/ui/tests/run/{submission.id}/{target_order}",
            status_code=303,
        )

    if action == "next":
        # общее число вопросов
        total_q = (
            db.query(QuizTestQuestion)
            .filter(QuizTestQuestion.test_id == test.id)
            .count()
        )
        target_order = min(order + 1, total_q)
        return RedirectResponse(
            url=f"/ui/tests/run/{submission.id}/{target_order}",
            status_code=303,
        )

    if action.startswith("goto_"):
        try:
            target_order = int(action.split("_", 1)[1])
        except ValueError:
            target_order = order
        return RedirectResponse(
            url=f"/ui/tests/run/{submission.id}/{target_order}",
            status_code=303,
        )

    if action == "finish":
        _score_submission(db, submission)
        return RedirectResponse(
            url=f"/ui/tests/result/{submission.id}",
            status_code=303,
        )

    # по умолчанию — просто остаться на текущем
    return RedirectResponse(
        url=f"/ui/tests/run/{submission.id}/{order}",
        status_code=303,
    )


# ---------- РЕЗУЛЬТАТЫ ----------


@router.get("/result/{submission_id}", response_class=HTMLResponse)
async def test_result(
    submission_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    submission = db.get(QuizSubmission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Попытка не найдена")

    test = submission.test

    if current_user.role not in ("admin", "teacher"):
        if submission.student_id != current_user.id:
            raise HTTPException(status_code=403, detail="Нет доступа")

    # если попытка ещё не подсчитана, подсчитаем
    if submission.finished_at is None or submission.total_score is None:
        _score_submission(db, submission)

    all_tq = (
        db.query(QuizTestQuestion)
        .filter(QuizTestQuestion.test_id == test.id)
        .order_by(QuizTestQuestion.order_index)
        .all()
    )

    answers_by_q: Dict[int, QuizSubmissionAnswer] = {
        a.question_id: a for a in submission.answers
    }

    rows = []
    for tq in all_tq:
        q = db.get(Question, tq.question_id)
        if not q:
            continue
        ans = answers_by_q.get(q.id)

        # получаем строковое представление правильного ответа
        correct_repr = ""
        answer_type = getattr(q, "answer_type", ans.answer_type if ans else "text")

        if answer_type == "text":
            correct_repr = getattr(q, "correct_text", "") or ""
        else:
            correct_ids = set(_get_correct_option_ids(db, q.id))
            if correct_ids:
                try:
                    options = (
                        db.query(Answer)
                        .filter(Answer.question_id == q.id)
                        .filter(Answer.id.in_(correct_ids))
                        .all()
                    )
                except Exception:
                    options = []
                texts = [getattr(o, "text", str(o.id)) for o in options]
                correct_repr = "; ".join(texts)

        rows.append(
            {
                "tq": tq,
                "question": q,
                "answer": ans,
                "correct_repr": correct_repr,
            }
        )

    return templates.TemplateResponse(
        "test_result.html",
        {
            "request": request,
            "user": current_user,
            "submission": submission,
            "test": test,
            "rows": rows,
        },
    )
