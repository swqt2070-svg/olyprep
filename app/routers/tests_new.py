from datetime import datetime
from typing import Dict, List, Optional, Tuple

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models import (
    Test,
    Question,
    Answer,
    TestAttempt,
    TestAttemptAnswer,
)

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(
    prefix="/ui/tests",
    tags=["ui-tests"],
)


# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------


def _get_test_or_404(db: Session, test_id: int) -> Test:
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Тест не найден")
    return test


def _get_questions_for_test(test: Test) -> List[Question]:
    """
    Возвращает список вопросов для теста в зафиксированном порядке.
    Предпочитаем test.test_questions (если есть), иначе test.questions.
    """
    # Через связующую таблицу (TestQuestion), если она есть
    if hasattr(test, "test_questions") and test.test_questions:
        ordered_links = sorted(
            test.test_questions,
            key=lambda link: getattr(link, "order", getattr(link, "position", link.id)),
        )
        questions: List[Question] = [link.question for link in ordered_links if link.question]
        return questions

    # Прямое many-to-many
    if hasattr(test, "questions") and test.questions:
        try:
            return sorted(test.questions, key=lambda q: getattr(q, "order", q.id))
        except Exception:
            return list(test.questions)

    return []


def _get_or_create_attempt(
    db: Session,
    test: Test,
    user_id: int,
) -> TestAttempt:
    """
    Берём последнюю попытку пользователя по тесту.
    Если нет — создаём новую.
    """
    stmt = (
        select(TestAttempt)
        .where(
            TestAttempt.test_id == test.id,
            TestAttempt.user_id == user_id,
        )
        .order_by(TestAttempt.id.desc())
    )
    attempt: Optional[TestAttempt] = db.scalars(stmt).first()

    if attempt is None:
        attempt = TestAttempt(test_id=test.id, user_id=user_id)
        if hasattr(attempt, "started_at"):
            attempt.started_at = datetime.utcnow()
        db.add(attempt)
        db.flush()

    return attempt


def _load_attempt_answers_map(
    db: Session,
    attempt: TestAttempt,
) -> Dict[int, TestAttemptAnswer]:
    """
    Все ответы попытки: question_id -> TestAttemptAnswer
    """
    stmt = select(TestAttemptAnswer).where(TestAttemptAnswer.attempt_id == attempt.id)
    items: List[TestAttemptAnswer] = list(db.scalars(stmt).all())
    return {item.question_id: item for item in items}


def _extract_answer_values(
    taa: Optional[TestAttemptAnswer],
) -> Tuple[Optional[int], str]:
    """
    Вытащить из ответа:
      - id выбранного варианта (для задач с вариантами),
      - текст ответа (для текстовых задач).
    Разные имена полей учитываем.
    """
    if taa is None:
        return None, ""

    selected_id: Optional[int] = None
    for field in ("answer_id", "selected_answer_id", "option_id"):
        if hasattr(taa, field):
            selected_id = getattr(taa, field)
            if selected_id is not None:
                break

    text_value = ""
    for field in ("answer_text", "text_answer", "value"):
        if hasattr(taa, field):
            text_value = getattr(taa, field) or ""
            if text_value:
                break

    return selected_id, text_value


def _save_answer_to_db(
    db: Session,
    attempt: TestAttempt,
    question: Question,
    answer_id: Optional[int],
    answer_text: str,
) -> None:
    """
    Записывает / обновляет TestAttemptAnswer для текущего вопроса.
    Вызывается ПЕРВЫМ делом при любом сабмите.
    """
    stmt = select(TestAttemptAnswer).where(
        TestAttemptAnswer.attempt_id == attempt.id,
        TestAttemptAnswer.question_id == question.id,
    )
    taa: Optional[TestAttemptAnswer] = db.scalars(stmt).first()

    if taa is None:
        taa = TestAttemptAnswer(
            attempt_id=attempt.id,
            question_id=question.id,
        )
        db.add(taa)

    # id выбранного варианта
    for field in ("answer_id", "selected_answer_id", "option_id"):
        if hasattr(taa, field):
            setattr(taa, field, answer_id)

    # текст ответа
    cleaned_text = (answer_text or "").strip()
    for field in ("answer_text", "text_answer", "value"):
        if hasattr(taa, field):
            setattr(taa, field, cleaned_text)


def _build_navigation(
    questions: List[Question],
    answers_map: Dict[int, TestAttemptAnswer],
    current_index: int,
) -> List[Dict]:
    """
    Данные для кружочков-навигации.
    """
    nav = []
    for idx, q in enumerate(questions):
        taa = answers_map.get(q.id)
        selected_id, text_val = _extract_answer_values(taa)
        answered = bool(selected_id is not None or (text_val and text_val.strip()))
        nav.append(
            {
                "number": idx + 1,
                "answered": answered,
                "current": (idx + 1) == current_index,
            }
        )
    return nav


# ---------- ROUTES ----------


@router.get("/run/{test_id}")
async def start_test(
    request: Request,
    test_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Старт теста -> редирект на 1‑й вопрос.
    """
    test = _get_test_or_404(db, test_id)
    questions = _get_questions_for_test(test)
    if not questions:
        raise HTTPException(status_code=400, detail="В тесте нет вопросов")

    _get_or_create_attempt(db, test, user.id)
    db.commit()

    return RedirectResponse(
        url=f"/ui/tests/run/{test_id}/1",
        status_code=302,
    )


@router.get("/run/{test_id}/{position}")
async def run_test_get(
    request: Request,
    test_id: int,
    position: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Показ вопроса № position.
    """
    test = _get_test_or_404(db, test_id)
    questions = _get_questions_for_test(test)
    if not questions:
        raise HTTPException(status_code=400, detail="В тесте нет вопросов")

    total = len(questions)
    if position < 1 or position > total:
        raise HTTPException(status_code=404, detail="Вопрос не найден")

    attempt = _get_or_create_attempt(db, test, user.id)
    answers_map = _load_attempt_answers_map(db, attempt)

    question = questions[position - 1]
    taa = answers_map.get(question.id)
    selected_answer_id, text_answer = _extract_answer_values(taa)

    # варианты ответа — только с непустым текстом
    options: List[Answer] = []
    if hasattr(question, "answers") and question.answers:
        for opt in question.answers:
            text = getattr(opt, "text", None)
            if text and str(text).strip():
                options.append(opt)

    nav = _build_navigation(questions, answers_map, position)
    max_score = getattr(test, "max_score", None)

    return templates.TemplateResponse(
        "test_run.html",
        {
            "request": request,
            "test": test,
            "attempt": attempt,
            "question": question,
            "position": position,
            "total_questions": total,
            "max_score": max_score,
            "options": options,
            "selected_answer_id": selected_answer_id,
            "text_answer": text_answer,
            "nav": nav,
        },
    )


@router.post("/run/{test_id}/{position}")
async def run_test_post(
    request: Request,
    test_id: int,
    position: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    # поля формы
    answer_id: Optional[int] = Form(None),
    answer_text: str = Form(""),
    action: str = Form("next"),
    goto: Optional[int] = Form(None),
):
    """
    Обработка ответа.
    1) ВСЕГДА сохраняем ответ в БД.
    2) Потом решаем, куда переходить:
       - prev / next
       - конкретный номер вопроса (кружки)
       - save (остаться на месте)
       - finish (завершить тест).
    """
    test = _get_test_or_404(db, test_id)
    questions = _get_questions_for_test(test)
    if not questions:
        raise HTTPException(status_code=400, detail="В тесте нет вопросов")

    total = len(questions)
    if position < 1 or position > total:
        raise HTTPException(status_code=404, detail="Вопрос не найден")

    attempt = _get_or_create_attempt(db, test, user.id)
    question = questions[position - 1]

    # 1. Сохраняем ответ
    _save_answer_to_db(
        db=db,
        attempt=attempt,
        question=question,
        answer_id=answer_id,
        answer_text=answer_text,
    )
    db.commit()

    # 2. Навигация
    if goto is not None:
        next_position = max(1, min(total, goto))
    else:
        action = (action or "next").lower()
        if action == "prev":
            next_position = max(1, position - 1)
        elif action == "save":
            next_position = position
        elif action == "finish":
            if hasattr(attempt, "finished_at"):
                attempt.finished_at = datetime.utcnow()
            db.commit()
            return RedirectResponse(
                url="/ui/account",
                status_code=303,
            )
        else:  # next
            next_position = min(total, position + 1)

    return RedirectResponse(
        url=f"/ui/tests/run/{test_id}/{next_position}",
        status_code=303,
    )
