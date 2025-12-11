from datetime import datetime
from typing import Dict, List, Optional

import json
import re
import random
from types import SimpleNamespace

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
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


def md_to_html(text: str) -> str:
    """
    Упрощённый Markdown → HTML для картинок и переносов строк.

    Поддерживаем только:
      * ![alt](url) → <img src="url" ...>
      * перевод строки → <br>
    Этого достаточно, чтобы показывать картинки в тексте задач и вариантов.
    """
    if not text:
        return ""

    # Картинки вида ![alt](/static/uploads/...)
    pattern = r"!\[[^\]]*\]\(\s*([^\)]+?)\s*\)"

    def _repl(match):
        url = match.group(1)
        return '<img src="' + url + '" style="max-width:100%;height:auto;" />'

    html = re.sub(pattern, _repl, str(text))
    # Переводы строк
    html = html.replace("\n", "<br>")
    return html


def _get_test_or_404(db: Session, test_id: int) -> Test:
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Тест не найден")
    return test


def _get_questions_for_test(db: Session, test: Test) -> List[TestQuestion]:
    """
    Возвращает список связок TestQuestion для теста в зафиксированном порядке.
    """
    tqs: List[TestQuestion] = (
        db.query(TestQuestion)
        .filter(TestQuestion.test_id == test.id)
        .order_by(TestQuestion.order.asc())
        .all()
    )
    return tqs


def _get_or_create_attempt(db: Session, test: Test, user_id: int) -> TestAttempt:
    """
    Берём незавершённую попытку теста для пользователя, либо создаём новую.
    """
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
        attempt = TestAttempt(
            test_id=test.id,
            user_id=user_id,
            started_at=datetime.utcnow(),
        )
        db.add(attempt)
        db.flush()  # чтобы появился id

    return attempt


def _load_attempt_answers_map(db: Session, attempt: TestAttempt) -> Dict[int, Answer]:
    """
    Словарь {question_id: Answer} для данной попытки.
    """
    answers: List[Answer] = (
        db.query(Answer)
        .filter(Answer.submission_id == attempt.id)
        .all()
    )
    return {a.question_id: a for a in answers}


def _extract_answer_values(answer: Optional[Answer]) -> tuple[Optional[int], str]:
    """
    Возвращает (selected_answer_id, answer_text) из объекта Answer.
    """
    if not answer:
        return None, ""
    selected_id = getattr(answer, "selected_option_id", None)
    text_val = getattr(answer, "answer_text", "") or ""
    return selected_id, text_val


def _get_options_for_question(question: Question) -> List[SimpleNamespace]:
    """
    Строит список вариантов ответа для вопроса.
    Возвращает SimpleNamespace(id=<int>, text=<str>, image_path=<str|None>).
    """
    options: List[SimpleNamespace] = []

    # 1) JSON в Question.options
    if getattr(question, "options", None):
        try:
            raw_opts = json.loads(question.options)
            for idx, text in enumerate(raw_opts):
                if text and str(text).strip():
                    options.append(
                        SimpleNamespace(id=idx, text=str(text), image_path=None)
                    )
        except Exception:
            options = []

    # 2) Ответы через question.option_items (AnswerOption)
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

    # 3) Фолбэк — question.answers (если есть старые данные)
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

    return options


def _recalculate_attempt_score(
    db: Session,
    attempt: TestAttempt,
    test: Test,
    tqs: List[TestQuestion],
) -> None:
    """
    Полный пересчёт score / max_score по всем вопросам теста.
    """
    answers_map = _load_attempt_answers_map(db, attempt)
    total_score = 0
    max_score = 0

    for link in tqs:
        q: Question = (
            link.question
            if hasattr(link, "question") and link.question
            else db.get(Question, link.question_id)
        )
        if hasattr(q, "question") and not hasattr(q, "options"):
            # на случай обёрнутых сущностей
            if q.question is not None:
                q = q.question  # type: ignore

        max_score += getattr(link, "points", 0) or 0
        ans = answers_map.get(q.id)
        if not ans:
            continue

        answer_type = getattr(q, "answer_type", "text") or "text"
        correct_str = (getattr(q, "correct", "") or "").strip()

        is_correct = False

        if answer_type in ("text", "number"):
            user_val = (getattr(ans, "answer_text", "") or "").strip()
            if not correct_str or not user_val:
                is_correct = False
            else:
                if answer_type == "number":
                    try:
                        gt = float(correct_str.replace(",", "."))
                        uv = float(user_val.replace(",", "."))
                        is_correct = gt == uv
                    except ValueError:
                        is_correct = False
                else:
                    is_correct = correct_str.lower() == user_val.lower()
        elif answer_type == "match":
            try:
                correct_list = json.loads(q.correct or "[]")
            except Exception:
                correct_list = []
            try:
                user_list = json.loads(getattr(ans, "answer_text", "") or "[]")
            except Exception:
                user_list = []
            if (
                isinstance(correct_list, list)
                and isinstance(user_list, list)
                and len(correct_list) == len(user_list)
                and all(
                    (user_list[i] is not None) and (int(user_list[i]) == int(correct_list[i]))
                    for i in range(len(correct_list))
                )
            ):
                is_correct = True
        elif answer_type in ("multi", "multiple"):
            # предполагаем, что correct хранит индексы через запятую, а в answer_text — тоже
            try:
                correct_idxs = {
                    int(x) for x in correct_str.split(",") if x.strip()
                }
            except ValueError:
                correct_idxs = set()
            user_idxs: set[int] = set()
            user_text = (getattr(ans, "answer_text", "") or "").strip()
            if user_text:
                try:
                    user_idxs = {int(x) for x in user_text.split(",") if x.strip()}
                except ValueError:
                    user_idxs = set()
            is_correct = bool(correct_idxs) and correct_idxs == user_idxs
        else:
            # single: correct — индекс варианта или is_correct у option_items
            selected_id = getattr(ans, "selected_option_id", None)
            if selected_id is None:
                is_correct = False
            else:
                # сначала пробуем через JSON-индекс
                if correct_str:
                    try:
                        correct_idx = int(correct_str)
                        is_correct = correct_idx == int(selected_id)
                    except ValueError:
                        is_correct = False
                # если не получилось — пробуем через option_items
                if not is_correct and hasattr(q, "option_items"):
                    for opt in getattr(q, "option_items") or []:
                        if (
                            getattr(opt, "id", None) == selected_id
                            and getattr(opt, "is_correct", False)
                        ):
                            is_correct = True
                            break

        ans.correct = bool(is_correct)
        ans.points = (getattr(link, "points", 0) or 0) if is_correct else 0
        total_score += ans.points

        db.add(ans)

    attempt.score = total_score
    attempt.max_score = max_score
    db.add(attempt)


# ---------- РОУТЫ ----------


@router.get("/run/{test_id}")
async def start_test(
    request: Request,
    test_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Старт теста -> редирект на 1-й вопрос.
    """
    test = _get_test_or_404(db, test_id)
    tqs = _get_questions_for_test(db, test)
    if not tqs:
        raise HTTPException(status_code=400, detail="В тесте нет вопросов")

    # Проверка лимита попыток
    if getattr(test, "max_attempts", None):
        attempts_count = (
            db.query(TestAttempt)
            .filter(TestAttempt.test_id == test.id, TestAttempt.user_id == user.id)
            .count()
        )
        if attempts_count >= test.max_attempts:
            raise HTTPException(
                status_code=400,
                detail="Достигнут лимит попыток для этого теста",
            )

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

    tqs: List[TestQuestion] = _get_questions_for_test(db, test)
    total = len(tqs)
    if total == 0:
        raise HTTPException(status_code=400, detail="В тесте нет вопросов")

    if position < 1 or position > total:
        raise HTTPException(status_code=404, detail="Вопрос не найден")

    attempt = _get_or_create_attempt(db, test, user.id)
    answers_map = _load_attempt_answers_map(db, attempt)

    link = tqs[position - 1]
    question = (
        link.question
        if hasattr(link, "question") and link.question
        else db.get(Question, link.question_id)
    )
    if hasattr(question, "question") and not hasattr(question, "options"):
        if question.question is not None:
            question = question.question  # type: ignore

    taa = answers_map.get(question.id)
    selected_answer_id, text_answer = _extract_answer_values(taa)

    options = _get_options_for_question(question)

    selected_answer_ids: List[int] = []
    match_left: List[dict] = []
    match_right: List[dict] = []
    match_selected: List[Optional[int]] = []

    if getattr(question, "answer_type", "text") == "match":
        try:
            pairs = json.loads(question.options or "[]")
        except Exception:
            pairs = []
        for idx, pair in enumerate(pairs):
            left = (pair.get("left") if isinstance(pair, dict) else None) or ""
            right = (pair.get("right") if isinstance(pair, dict) else None) or ""
            if left or right:
                match_left.append({"index": idx, "text": left})
                match_right.append({"index": idx, "text": right})
        random.shuffle(match_right)
        if taa and getattr(taa, "answer_text", ""):
            try:
                match_selected = json.loads(getattr(taa, "answer_text", "") or "[]")
            except Exception:
                match_selected = []
        if not match_selected:
            match_selected = [None] * len(match_left)
    else:
        if getattr(question, "answer_type", "text") in ("multi", "multiple"):
            raw_multi = text_answer or ""
            if raw_multi:
                try:
                    selected_answer_ids = [int(x) for x in raw_multi.split(",") if x.strip()]
                except Exception:
                    selected_answer_ids = []
            if not selected_answer_ids and selected_answer_id is not None:
                selected_answer_ids = [selected_answer_id]
            selected_answer_id = selected_answer_ids[0] if selected_answer_ids else None
            text_answer = ""

    # HTML-версии текста вопроса и вариантов с поддержкой ![](url)
    question_html = md_to_html(getattr(question, "text", None) or "")
    answers_html = [md_to_html(getattr(opt, "text", None) or "") for opt in options]

    # Навигация/подсветка уже не используется в шаблоне, но передаём пустой список для совместимости
    nav: List[dict] = []

    # Максимальный балл за текущий вопрос
    max_points_for_question = getattr(link, "points", 0) or 0

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
            "question_html": question_html,
            "answers_html": answers_html,
            "max_points": max_points_for_question,
            "answers": options,
            "selected_answer_id": selected_answer_id,
            "selected_answer_ids": selected_answer_ids,
            "answer_text": text_answer,
            "state_json": "",
            "nav": nav,
            "match_left": match_left,
            "match_right": match_right,
            "match_selected": match_selected,
        },
    )


@router.post("/run/{test_id}/{position}")
async def run_test_post(
    request: Request,
    test_id: int,
    position: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Обработка ответа.
    1) Всегда сохраняем ответ в БД.
    2) Потом решаем, куда переходить:
       - prev / next
       - goto (конкретный номер вопроса)
       - save (остаться)
       - finish (завершить тест).
    """
    form = await request.form()
    action = (form.get("action") or "next").strip()
    goto_raw = form.get("goto")
    question_id_raw = form.get("question_id")
    answer_text = (form.get("answer_text") or "").strip()

    # single
    selected_answer_id = form.get("selected_answer_id")
    selected_answer_id_int: Optional[int] = None
    if selected_answer_id not in (None, ""):
        try:
            selected_answer_id_int = int(selected_answer_id)
        except ValueError:
            selected_answer_id_int = None

    # multi: список индексов/ID
    multi_ids_raw = form.getlist("selected_answer_ids") if "selected_answer_ids" in form else []
    multi_ids: List[int] = []
    for v in multi_ids_raw:
        try:
            multi_ids.append(int(v))
        except ValueError:
            continue

    # match: список соответствий left_index -> right_index
    match_choices: List[Optional[int]] = []

    test = _get_test_or_404(db, test_id)
    tqs: List[TestQuestion] = _get_questions_for_test(db, test)
    total = len(tqs)
    if total == 0:
        raise HTTPException(status_code=400, detail="В тесте нет вопросов")

    if position < 1 or position > total:
        raise HTTPException(status_code=404, detail="Вопрос не найден")

    attempt = _get_or_create_attempt(db, test, user.id)

    if not question_id_raw:
        raise HTTPException(status_code=400, detail="Не указан вопрос")
    try:
        question_id = int(question_id_raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный идентификатор вопроса")

    question = db.get(Question, question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Вопрос не найден")

    answer_type = getattr(question, "answer_type", "text") or "text"

    if answer_type == "match":
        try:
            pairs = json.loads(question.options or "[]")
        except Exception:
            pairs = []
        for i in range(len(pairs)):
            val = form.get(f"match_choice_{i}")
            try:
                match_choices.append(int(val)) if val not in (None, "") else match_choices.append(None)
            except ValueError:
                match_choices.append(None)

    # Сохраняем/обновляем Answer
    ans: Optional[Answer] = (
        db.query(Answer)
        .filter(
            Answer.submission_id == attempt.id,
            Answer.question_id == question.id,
        )
        .first()
    )

    if ans is None:
        ans = Answer(
            submission_id=attempt.id,
            question_id=question.id,
        )

    if answer_type == "match":
        ans.answer_text = json.dumps(match_choices)
        ans.selected_option_id = None
    elif answer_type in ("multi", "multiple"):
        # храним выбранные индексы через запятую
        if multi_ids:
            ans.answer_text = ",".join(str(i) for i in sorted(set(multi_ids)))
        else:
            ans.answer_text = ""
        ans.selected_option_id = None
    elif answer_type in ("text", "number"):
        ans.answer_text = answer_text
        ans.selected_option_id = None
    else:
        # single
        ans.selected_option_id = selected_answer_id_int
        ans.answer_text = answer_text

    db.add(ans)
    db.flush()

    # Пересчёт баллов
    _recalculate_attempt_score(db, attempt, test, tqs)

    # Решаем, куда идти дальше
    next_position = position

    if action == "prev":
        next_position = max(1, position - 1)
    elif action == "next":
        next_position = min(total, position + 1)
    elif action == "goto":
        if goto_raw:
            try:
                g = int(goto_raw)
                if 1 <= g <= total:
                    next_position = g
            except ValueError:
                pass
    elif action == "save":
        next_position = position
    elif action == "finish":
        # Завершаем попытку
        attempt.finished_at = datetime.utcnow()
        db.add(attempt)
        db.commit()
        return RedirectResponse(url="/ui/account", status_code=303)

    db.commit()

    return RedirectResponse(
        url=f"/ui/tests/run/{test_id}/{next_position}",
        status_code=303,
    )
