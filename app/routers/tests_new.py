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
import json
from types import SimpleNamespace

from app.deps import get_db, get_current_user
from app.models import (
    Test,
    Question,
    Answer,
    TestQuestion,
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
    attempt: Optional[TestAttempt] = None
    if hasattr(TestAttempt, "finished_at"):
        stmt_active = (
            select(TestAttempt)
            .where(
                TestAttempt.test_id == test.id,
                TestAttempt.user_id == user_id,
                TestAttempt.finished_at.is_(None),
            )
            .order_by(TestAttempt.id.desc())
        )
        attempt = db.scalars(stmt_active).first()

    if attempt is None:
        stmt_last = (
            select(TestAttempt)
            .where(
                TestAttempt.test_id == test.id,
                TestAttempt.user_id == user_id,
            )
            .order_by(TestAttempt.id.desc())
        )
        attempt = db.scalars(stmt_last).first()

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
    answer_ids: Optional[List[int]],
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

    # подготовка выбранных вариантов (для multi держим список в текстовом поле)
    normalized_ids: List[int] = []
    if answer_ids:
        for raw in answer_ids:
            try:
                normalized_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        normalized_ids = sorted(set(normalized_ids))
        if normalized_ids:
            answer_id = normalized_ids[0]

    # id выбранного варианта
    for field in ("answer_id", "selected_answer_id", "option_id"):
        if hasattr(taa, field):
            setattr(taa, field, answer_id)

    # текст ответа
    cleaned_text = (answer_text or "").strip()
    if normalized_ids:
        cleaned_text = json.dumps(normalized_ids)
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

    if getattr(test, "max_attempts", None):
        attempts_count = (
            db.query(TestAttempt)
            .filter(TestAttempt.test_id == test.id, TestAttempt.user_id == user.id)
            .count()
        )
        if attempts_count >= test.max_attempts:
            raise HTTPException(status_code=400, detail="Достигнут лимит попыток для этого теста")

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

    tqs: List[TestQuestion] = (
        db.query(TestQuestion)
        .filter(TestQuestion.test_id == test_id)
        .order_by(TestQuestion.order.asc())
        .all()
    )
    if not tqs:
        raise HTTPException(status_code=400, detail="В тесте нет вопросов")

    total = len(tqs)
    if position < 1 or position > total:
        raise HTTPException(status_code=404, detail="Вопрос не найден")

    attempt = _get_or_create_attempt(db, test, user.id)
    answers_map = _load_attempt_answers_map(db, attempt)

    link = tqs[position - 1]
    question = link.question if hasattr(link, "question") and link.question else db.get(Question, link.question_id)
    if hasattr(question, "question") and not hasattr(question, "options"):
        if question.question is not None:
            question = question.question
    taa = answers_map.get(question.id)
    selected_answer_id, text_answer = _extract_answer_values(taa)
    selected_answer_ids: List[int] = []
    if getattr(question, "answer_type", "text") in ("multi", "multiple"):
        raw_multi = ""
        if taa is not None:
            raw_multi = getattr(taa, "answer_text", "") or getattr(taa, "value", "")
        try:
            decoded = json.loads(raw_multi) if raw_multi else []
            if isinstance(decoded, list):
                selected_answer_ids = [int(x) for x in decoded if str(x).strip() != ""]
        except Exception:
            selected_answer_ids = []
        if not selected_answer_ids and selected_answer_id is not None:
            selected_answer_ids = [selected_answer_id]
        text_answer = ""

    # варианты ответа — только с непустым текстом
    options: List[Answer] = []
    if question.options:
        try:
            raw_opts = json.loads(question.options)
            for idx, text in enumerate(raw_opts):
                if text and str(text).strip():
                    options.append(SimpleNamespace(id=idx, text=text, image_path=None))
        except Exception:
            options = []
    if not options and hasattr(question, "option_items"):
        for opt in getattr(question, "option_items") or []:
            if opt and getattr(opt, "text", None):
                options.append(
                    SimpleNamespace(
                        id=getattr(opt, "id", None),
                        text=opt.text,
                        image_path=getattr(opt, "image_path", None),
                    )
                )
    if not options and hasattr(question, "answers") and question.answers:
        for opt in question.answers:
            text = getattr(opt, "text", None)
            if text and str(text).strip():
                options.append(
                    SimpleNamespace(
                        id=getattr(opt, "id", None),
                        text=text,
                        image_path=getattr(opt, "image_path", None),
                    )
                )

    questions_for_nav = [q.question if hasattr(q, "question") and q.question else q for q in tqs]
    nav = _build_navigation(questions_for_nav, answers_map, position)
    max_score = sum((getattr(tq, "points", 0) or 0) for tq in tqs)

    return templates.TemplateResponse(
        "test_run.html",
        {
            "request": request,
            "test": test,
            "attempt": attempt,
            "question": question,
            "position": position,
            "index": position - 1,
            "total_questions": total,
            "max_points": max_score,
            "answers": options,
            "selected_answer_id": selected_answer_id,
            "selected_answer_ids": selected_answer_ids,
            "answer_text": text_answer,
            "state_json": "",
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
    # ответ с варианта/текста
    answer_id: Optional[int] = Form(None),
    selected_answer_id: Optional[int] = Form(None),
    selected_answer_ids: Optional[List[int]] = Form(None),
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
    tqs: List[TestQuestion] = (
        db.query(TestQuestion)
        .filter(TestQuestion.test_id == test_id)
        .order_by(TestQuestion.order.asc())
        .all()
    )
    if not tqs:
        raise HTTPException(status_code=400, detail="В тесте нет вопросов")

    total = len(tqs)
    if position < 1 or position > total:
        raise HTTPException(status_code=404, detail="Вопрос не найден")

    attempt = _get_or_create_attempt(db, test, user.id)
    link = tqs[position - 1]
    question = link.question if hasattr(link, "question") and link.question else db.get(Question, link.question_id)
    if hasattr(question, "question") and not hasattr(question, "options"):
        if question.question is not None:
            question = question.question

    if answer_id is None and selected_answer_id is not None:
        answer_id = selected_answer_id

    selected_ids_list: List[int] = []
    if selected_answer_ids:
        for raw in selected_answer_ids:
            try:
                selected_ids_list.append(int(raw))
            except (TypeError, ValueError):
                continue
    if not selected_ids_list and answer_id is not None:
        selected_ids_list = [answer_id]

    # 1. Сохраняем ответ
    _save_answer_to_db(
        db=db,
        attempt=attempt,
        question=question,
        answer_id=answer_id,
        answer_ids=selected_ids_list if getattr(question, "answer_type", "text") in ("multi", "multiple") else None,
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
            # Подсчёт результата
            answers_map = _load_attempt_answers_map(db, attempt)
            score = 0
            max_points = sum((getattr(tq, "points", 0) or 0) for tq in tqs)
            for link in tqs:
                q = link.question if hasattr(link, "question") and link.question else db.get(Question, link.question_id)
                if hasattr(q, "question") and not hasattr(q, "options"):
                    if q.question is not None:
                        q = q.question
                taa = answers_map.get(q.id)
                selected_id, text_val = _extract_answer_values(taa)
                selected_ids: List[int] = []
                if getattr(q, "answer_type", "text") in ("multi", "multiple"):
                    raw_multi = ""
                    if taa is not None:
                        raw_multi = getattr(taa, "answer_text", "") or getattr(taa, "value", "")
                    try:
                        decoded = json.loads(raw_multi) if raw_multi else []
                        if isinstance(decoded, list):
                            selected_ids = [int(x) for x in decoded if str(x).strip() != ""]
                    except Exception:
                        selected_ids = []
                    if not selected_ids and selected_id is not None:
                        selected_ids = [selected_id]

                answer_type = getattr(q, "answer_type", "text")
                points_for_q = getattr(link, "points", 0) or 0

                if answer_type == "text":
                    gt = (q.correct or "").strip().lower() if hasattr(q, "correct") else ""
                    uv = (text_val or "").strip().lower()
                    if gt and uv and gt == uv:
                        score += points_for_q
                elif answer_type == "number":
                    try:
                        gt_num = float(q.correct) if q.correct is not None else None
                        uv_num = float(text_val) if text_val not in (None, "") else None
                    except (TypeError, ValueError):
                        gt_num = uv_num = None
                    if gt_num is not None and uv_num is not None and gt_num == uv_num:
                        score += points_for_q
                elif answer_type in ("multi", "multiple"):
                    try:
                        correct_multi = json.loads(q.correct) if q.correct else []
                        correct_set = {int(x) for x in correct_multi}
                    except Exception:
                        correct_set = set()
                    user_set = {int(x) for x in selected_ids} if selected_ids else set()
                    if correct_set and user_set and correct_set == user_set:
                        score += points_for_q
                else:
                    try:
                        correct_idx = int(q.correct) if q.correct is not None else None
                    except (TypeError, ValueError):
                        correct_idx = None
                    try:
                        user_idx = int(selected_id) if selected_id is not None else None
                    except (TypeError, ValueError):
                        user_idx = None
                    if correct_idx is not None and user_idx is not None and correct_idx == user_idx:
                        score += points_for_q

            if hasattr(attempt, "finished_at"):
                attempt.finished_at = datetime.utcnow()
            if hasattr(attempt, "score"):
                attempt.score = score
            if hasattr(attempt, "max_score"):
                attempt.max_score = max_points
            db.add(attempt)
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
