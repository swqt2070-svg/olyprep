from fastapi import (
    APIRouter,
    Depends,
    Request,
    Form,
    status,
    HTTPException,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List, Optional
import json

from app.deps import get_db, get_current_user, require_role
from app.models import User, Question, Test, TestQuestion, Submission, Answer
from app.security import hash_password, verify_password, create_token

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="app/templates")

# Коды приглашения — при желании замени
STUDENT_INVITE_CODE = "STUDENT2025"
TEACHER_INVITE_CODE = "TEACHER2025"


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


# ---------- ВСПОМОГАТЕЛЬНОЕ: контекст для ЛК ----------


def build_account_context(
    request: Request,
    db: Session,
    user: User,
    password_error: Optional[str] = None,
    password_success: Optional[str] = None,
):
    student_results = None
    teacher_results = None

    # История попыток ученика
    if user.role == "student":
        submissions: List[Submission] = (
            db.query(Submission)
            .filter(Submission.user_id == user.id)
            .order_by(Submission.id.desc())
            .all()
        )
        results = []
        for sub in submissions:
            test = db.get(Test, sub.test_id)
            if not test:
                continue
            tqs = (
                db.query(TestQuestion)
                .filter(TestQuestion.test_id == test.id)
                .all()
            )
            max_points = sum(tq.points for tq in tqs) if tqs else 0
            results.append(
                {
                    "submission": sub,
                    "test": test,
                    "max_points": max_points,
                }
            )
        student_results = results

    # Результаты учеников (teacher/admin — по всем тестам, пока без фильтров)
    if user.role in ("teacher", "admin"):
        students: List[User] = db.query(User).filter(User.role == "student").all()
        students_map = {s.id: s for s in students}
        student_ids = list(students_map.keys())

        if student_ids:
            submissions2: List[Submission] = (
                db.query(Submission)
                .filter(Submission.user_id.in_(student_ids))
                .order_by(Submission.id.desc())
                .all()
            )
        else:
            submissions2 = []

        tests: List[Test] = db.query(Test).all()
        tests_map = {t.id: t for t in tests}
        test_ids = [t.id for t in tests]

        if test_ids:
            tqs2: List[TestQuestion] = (
                db.query(TestQuestion)
                .filter(TestQuestion.test_id.in_(test_ids))
                .all()
            )
            max_points_map: dict[int, int] = {}
            for tq in tqs2:
                max_points_map.setdefault(tq.test_id, 0)
                max_points_map[tq.test_id] += tq.points
        else:
            max_points_map = {}

        rows = []
        for sub in submissions2:
            student = students_map.get(sub.user_id)
            test = tests_map.get(sub.test_id)
            if not student or not test:
                continue
            max_points = max_points_map.get(test.id, 0)
            rows.append(
                {
                    "submission": sub,
                    "student": student,
                    "test": test,
                    "max_points": max_points,
                }
            )

        teacher_results = rows

    return {
        "request": request,
        "user": user,
        "password_error": password_error,
        "password_success": password_success,
        "student_results": student_results,
        "teacher_results": teacher_results,
    }


# ---------- AUTH UI ----------


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "user": None, "error": None},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "user": None, "error": "Неверная почта или пароль"},
            status_code=400,
        )

    token = create_token({"id": user.id, "role": user.role})
    response = redirect("/ui/dashboard")
    response.set_cookie("access_token", token, httponly=True)
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "user": None, "error": None, "success": None},
    )


@router.post("/register")
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    invite_code: str = Form(""),
    db: Session = Depends(get_db),
):
    email = email.strip()
    invite_code = invite_code.strip()

    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "user": None,
                "error": "Такая почта уже используется",
                "success": None,
            },
            status_code=400,
        )

    has_admin = db.query(User).filter(User.role == "admin").first() is not None

    # первый пользователь — admin без кода
    if not has_admin:
        role = "admin"
    else:
        if not invite_code:
            return templates.TemplateResponse(
                "register.html",
                {
                    "request": request,
                    "user": None,
                    "error": "Для регистрации нужен код приглашения. Получите его у учителя или администратора.",
                    "success": None,
                },
                status_code=400,
            )
        if invite_code == STUDENT_INVITE_CODE:
            role = "student"
        elif invite_code == TEACHER_INVITE_CODE:
            role = "teacher"
        else:
            return templates.TemplateResponse(
                "register.html",
                {
                    "request": request,
                    "user": None,
                    "error": "Неверный код приглашения.",
                    "success": None,
                },
                status_code=400,
            )

    user = User(email=email, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()

    token = create_token({"id": user.id, "role": user.role})
    response = redirect("/ui/dashboard")
    response.set_cookie("access_token", token, httponly=True)
    return response


@router.post("/logout")
async def logout():
    response = redirect("/ui/login")
    response.delete_cookie("access_token")
    return response


# ---------- DASHBOARD ----------


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "user": user},
    )


# ---------- ЛИЧНЫЙ КАБИНЕТ (/ui/account) ----------


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx = build_account_context(request, db, user)
    return templates.TemplateResponse("account.html", ctx)


@router.post("/account/change-password", response_class=HTMLResponse)
async def account_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password2: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    error = None
    success = None

    if not verify_password(current_password, user.password_hash):
        error = "Текущий пароль введён неверно."
    elif len(new_password) < 6:
        error = "Новый пароль должен быть не короче 6 символов."
    elif new_password != new_password2:
        error = "Пароль и подтверждение не совпадают."
    else:
        user.password_hash = hash_password(new_password)
        db.add(user)
        db.commit()
        success = "Пароль успешно обновлён."

    ctx = build_account_context(request, db, user, error, success)
    return templates.TemplateResponse(
        "account.html",
        ctx,
        status_code=400 if error else 200,
    )


# ---------- ADMIN: пользователи ----------


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    users = db.query(User).order_by(User.id.asc()).all()
    return templates.TemplateResponse(
        "users_admin.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "error": None,
            "success": None,
        },
    )


@router.post("/admin/users/set-role", response_class=HTMLResponse)
async def admin_set_role(
    request: Request,
    email: str = Form(...),
    role: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    allowed_roles = ("admin", "teacher", "student")

    email = email.strip()
    role = role.strip()

    error: Optional[str] = None
    success: Optional[str] = None

    if role not in allowed_roles:
        error = "Недопустимая роль."
    else:
        target = db.query(User).filter(User.email == email).first()
        if not target:
            error = "Пользователь с такой почтой не найден."
        elif target.id == user.id and role != "admin":
            error = "Нельзя понизить роль собственного админ‑аккаунта."
        else:
            old_role = target.role
            target.role = role
            db.add(target)
            db.commit()
            success = f"Роль пользователя {email} изменена с {old_role} на {role}."

    users = db.query(User).order_by(User.id.asc()).all()
    status_code = 400 if error else 200
    return templates.TemplateResponse(
        "users_admin.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "error": error,
            "success": success,
        },
        status_code=status_code,
    )


# ---------- QUESTIONS UI (список / создание / редактирование / удаление) ----------


@router.get("/questions", response_class=HTMLResponse)
async def questions_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows: List[Question] = db.query(Question).order_by(Question.id.desc()).all()
    return templates.TemplateResponse(
        "questions_list.html",
        {
            "request": request,
            "user": user,
            "questions": rows,
            "error": None,
            "success": None,
        },
    )


@router.get("/questions/new", response_class=HTMLResponse)
async def question_new_page(
    request: Request,
    user: User = Depends(require_role("admin", "teacher")),
):
    return templates.TemplateResponse(
        "question_new.html",
        {"request": request, "user": user, "error": None, "success": None},
    )


@router.post("/questions/new", response_class=HTMLResponse)
async def question_new_submit(
    request: Request,
    text: str = Form(...),
    answer_type: str = Form(...),
    correct_text: str = Form(""),
    correct_index: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    form = await request.form()
    error: Optional[str] = None

    answer_type = answer_type.strip()

    if answer_type not in ("text", "single", "multi"):
        error = "Неверный тип ответа."

    # TEXT
    if not error and answer_type == "text":
        if not correct_text.strip():
            error = "Укажите правильный текстовый ответ."

    # SINGLE / MULTI — собираем варианты
    raw_options: List[str] = []
    if not error and answer_type in ("single", "multi"):
        for idx in range(4):
            val = form.get(f"option_{idx}", "").strip()
            if val:
                raw_options.append(val)
        if not raw_options:
            error = "Укажите хотя бы один вариант ответа."

    # SINGLE — проверка индекса
    if not error and answer_type == "single":
        if correct_index == "":
            error = "Выберите, какой вариант считать правильным."

    # MULTI — проверка набора правильных вариантов
    correct_multi_indices: List[str] = []
    if not error and answer_type == "multi":
        correct_multi_indices = form.getlist("correct_multi")
        correct_multi_indices = [x for x in correct_multi_indices if x != ""]
        if not correct_multi_indices:
            error = "Отметьте хотя бы один правильный вариант."

    if error:
        return templates.TemplateResponse(
            "question_new.html",
            {"request": request, "user": user, "error": error, "success": None},
            status_code=400,
        )

    # Сохраняем
    if answer_type == "text":
        options_json = None
        correct = correct_text.strip()
    elif answer_type == "single":
        options_json = json.dumps(raw_options, ensure_ascii=False)
        correct = correct_index  # строка с индексом
    else:  # multi
        options_json = json.dumps(raw_options, ensure_ascii=False)
        indices: List[int] = []
        for x in correct_multi_indices:
            try:
                indices.append(int(x))
            except ValueError:
                continue
        indices = sorted(set(indices))
        correct = json.dumps(indices, ensure_ascii=False)

    q = Question(
        text=text,
        answer_type=answer_type,
        options=options_json,
        correct=correct,
    )
    db.add(q)
    db.commit()

    return templates.TemplateResponse(
        "question_new.html",
        {
            "request": request,
            "user": user,
            "error": None,
            "success": f"Задача успешно сохранена. ID: {q.id}",
        },
    )


@router.get("/questions/{question_id}/edit", response_class=HTMLResponse)
async def question_edit_page(
    question_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(status_code=404, detail="question not found")

    options = json.loads(q.options) if q.options else []
    correct_text = ""
    correct_index = ""
    correct_multi_indices: List[int] = []

    if q.answer_type == "text":
        correct_text = q.correct or ""
    elif q.answer_type == "single":
        correct_index = q.correct or ""
    elif q.answer_type == "multi":
        try:
            arr = json.loads(q.correct or "[]")
            correct_multi_indices = [int(x) for x in arr]
        except Exception:
            correct_multi_indices = []

    return templates.TemplateResponse(
        "question_edit.html",
        {
            "request": request,
            "user": user,
            "question": q,
            "options": options,
            "correct_text": correct_text,
            "correct_index": correct_index,
            "correct_multi_indices": correct_multi_indices,
            "error": None,
            "success": None,
        },
    )


@router.post("/questions/{question_id}/edit", response_class=HTMLResponse)
async def question_edit_submit(
    question_id: int,
    request: Request,
    text: str = Form(...),
    answer_type: str = Form(...),
    correct_text: str = Form(""),
    correct_index: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(status_code=404, detail="question not found")

    form = await request.form()
    error: Optional[str] = None

    answer_type = answer_type.strip()

    if answer_type not in ("text", "single", "multi"):
        error = "Неверный тип ответа."

    # TEXT
    if not error and answer_type == "text":
        if not correct_text.strip():
            error = "Укажите правильный текстовый ответ."

    raw_options: List[str] = []
    if not error and answer_type in ("single", "multi"):
        for idx in range(4):
            val = form.get(f"option_{idx}", "").strip()
            if val:
                raw_options.append(val)
        if not raw_options:
            error = "Укажите хотя бы один вариант ответа."

    if not error and answer_type == "single":
        if correct_index == "":
            error = "Выберите, какой вариант считать правильным."

    correct_multi_indices: List[str] = []
    if not error and answer_type == "multi":
        correct_multi_indices = form.getlist("correct_multi")
        correct_multi_indices = [x for x in correct_multi_indices if x != ""]
        if not correct_multi_indices:
            error = "Отметьте хотя бы один правильный вариант."

    if error:
        options = json.loads(q.options) if q.options else []
        existing_correct_text = ""
        existing_correct_index = ""
        existing_multi: List[int] = []

        if q.answer_type == "text":
            existing_correct_text = q.correct or ""
        elif q.answer_type == "single":
            existing_correct_index = q.correct or ""
        elif q.answer_type == "multi":
            try:
                arr = json.loads(q.correct or "[]")
                existing_multi = [int(x) for x in arr]
            except Exception:
                existing_multi = []

        return templates.TemplateResponse(
            "question_edit.html",
            {
                "request": request,
                "user": user,
                "question": q,
                "options": options,
                "correct_text": existing_correct_text,
                "correct_index": existing_correct_index,
                "correct_multi_indices": existing_multi,
                "error": error,
                "success": None,
            },
            status_code=400,
        )

    # Перезаписываем данные вопроса
    if answer_type == "text":
        options_json = None
        correct = correct_text.strip()
    elif answer_type == "single":
        options_json = json.dumps(raw_options, ensure_ascii=False)
        correct = correct_index
    else:  # multi
        options_json = json.dumps(raw_options, ensure_ascii=False)
        indices: List[int] = []
        for x in correct_multi_indices:
            try:
                indices.append(int(x))
            except ValueError:
                continue
        indices = sorted(set(indices))
        correct = json.dumps(indices, ensure_ascii=False)

    q.text = text
    q.answer_type = answer_type
    q.options = options_json
    q.correct = correct
    db.add(q)
    db.commit()

    # Заново считаем вспомогательные поля для шаблона
    options = json.loads(q.options) if q.options else []
    correct_text_val = ""
    correct_index_val = ""
    correct_multi_val: List[int] = []

    if q.answer_type == "text":
        correct_text_val = q.correct or ""
    elif q.answer_type == "single":
        correct_index_val = q.correct or ""
    elif q.answer_type == "multi":
        try:
            arr = json.loads(q.correct or "[]")
            correct_multi_val = [int(x) for x in arr]
        except Exception:
            correct_multi_val = []

    return templates.TemplateResponse(
        "question_edit.html",
        {
            "request": request,
            "user": user,
            "question": q,
            "options": options,
            "correct_text": correct_text_val,
            "correct_index": correct_index_val,
            "correct_multi_indices": correct_multi_val,
            "error": None,
            "success": "Изменения сохранены.",
        },
    )


@router.post("/questions/{question_id}/delete", response_class=HTMLResponse)
async def question_delete(
    question_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    error: Optional[str] = None
    success: Optional[str] = None

    q = db.get(Question, question_id)
    if not q:
        error = f"Вопрос #{question_id} не найден."
    else:
        # Удаляем зависимые TestQuestion и Answer
        db.query(Answer).filter(Answer.question_id == question_id).delete()
        db.query(TestQuestion).filter(TestQuestion.question_id == question_id).delete()
        db.delete(q)
        db.commit()
        success = f"Вопрос #{question_id} удалён (также убран из тестов и ответов)."

    rows: List[Question] = db.query(Question).order_by(Question.id.desc()).all()
    return templates.TemplateResponse(
        "questions_list.html",
        {
            "request": request,
            "user": user,
            "questions": rows,
            "error": error,
            "success": success,
        },
        status_code=400 if error else 200,
    )


# ---------- TESTS UI ----------


@router.get("/tests", response_class=HTMLResponse)
async def tests_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tests = db.query(Test).order_by(Test.id.desc()).all()
    return templates.TemplateResponse(
        "tests_list.html",
        {"request": request, "user": user, "tests": tests},
    )


@router.post("/tests/new")
async def tests_new(
    title: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    t = Test(title=title.strip())
    db.add(t)
    db.commit()
    return redirect("/ui/tests")


@router.get("/tests/{test_id}", response_class=HTMLResponse)
async def test_view(
    test_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    submission_id: Optional[int] = None,
):
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="test not found")

    tqs: List[TestQuestion] = (
        db.query(TestQuestion)
        .filter(TestQuestion.test_id == test_id)
        .all()
    )
    items = []
    max_points = 0
    for tq in tqs:
        q = db.get(Question, tq.question_id)
        if not q:
            continue
        opts = json.loads(q.options) if q.options else None
        max_points += tq.points
        items.append({"tq": tq, "q": q, "options": opts})

    submission = None
    if submission_id is not None:
        submission = db.get(Submission, submission_id)
        if not submission or submission.user_id != user.id:
            submission = None

    return templates.TemplateResponse(
        "test_run.html",
        {
            "request": request,
            "user": user,
            "test": test,
            "items": items,
            "max_points": max_points,
            "submission": submission,
            "result": None,
        },
    )


@router.post("/tests/{test_id}/add-question")
async def test_add_question(
    test_id: int,
    question_id: int = Form(...),
    points: int = Form(1),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="test not found")

    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(status_code=404, detail="question not found")

    tq = TestQuestion(test_id=test_id, question_id=question_id, points=points)
    db.add(tq)
    db.commit()
    return redirect(f"/ui/tests/{test_id}")


@router.post("/tests/{test_id}/start")
async def test_start(
    test_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="test not found")

    s = Submission(test_id=test_id, user_id=user.id, score=0)
    db.add(s)
    db.commit()
    return redirect(f"/ui/tests/{test_id}?submission_id={s.id}")


@router.post("/tests/{test_id}/submit", response_class=HTMLResponse)
async def test_submit(
    test_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    form = await request.form()
    submission_id = int(form.get("submission_id", "0"))
    submission = db.get(Submission, submission_id)

    if not submission or submission.user_id != user.id or submission.test_id != test_id:
        raise HTTPException(status_code=400, detail="invalid submission")

    tqs: List[TestQuestion] = (
        db.query(TestQuestion)
        .filter(TestQuestion.test_id == test_id)
        .all()
    )

    items = []
    max_points = 0
    score = 0

    for tq in tqs:
        q = db.get(Question, tq.question_id)
        if not q:
            continue

        field_name = f"answer_{q.id}"
        opts = json.loads(q.options) if q.options else None
        max_points += tq.points

        correct_flag = 0
        earned = 0
        saved_given = ""

        if q.answer_type == "multi":
            selected = form.getlist(field_name)
            selected = [s for s in selected if s != ""]
            if selected:
                try:
                    selected_idx = sorted(int(x) for x in selected)
                except ValueError:
                    selected_idx = []
                try:
                    correct_indices = json.loads(q.correct or "[]")
                except json.JSONDecodeError:
                    correct_indices = []
                except TypeError:
                    correct_indices = []
                correct_indices = (
                    sorted(int(x) for x in correct_indices)
                    if correct_indices
                    else []
                )
                if selected_idx and selected_idx == correct_indices:
                    correct_flag = 1
                    earned = tq.points
                saved_given = json.dumps(selected_idx, ensure_ascii=False)
        else:
            given = form.get(field_name, "").strip()
            saved_given = given

            if given:
                if q.answer_type == "text":
                    gt = (q.correct or "").strip().lower()
                    uv = given.strip().lower()
                    ok = gt == uv
                    correct_flag = 1 if ok else 0
                    earned = tq.points if ok else 0
                elif q.answer_type == "single":
                    ok = (q.correct == given)
                    correct_flag = 1 if ok else 0
                    earned = tq.points if ok else 0
                else:
                    correct_flag = 0
                    earned = 0

        ans = Answer(
            submission_id=submission.id,
            question_id=q.id,
            given=saved_given,
            correct=correct_flag,
            points=earned,
        )
        db.add(ans)
        score += earned

        items.append({"tq": tq, "q": q, "options": opts})

    submission.score = score
    db.add(submission)
    db.commit()

    test = db.get(Test, test_id)
    result = {"score": score, "max_points": max_points}

    return templates.TemplateResponse(
        "test_run.html",
        {
            "request": request,
            "user": user,
            "test": test,
            "items": items,
            "max_points": max_points,
            "submission": submission,
            "result": result,
        },
    )
