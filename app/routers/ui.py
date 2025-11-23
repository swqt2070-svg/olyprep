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
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from app.deps import get_db, get_current_user, require_role
from app.models import User, Question, Test, TestQuestion, Submission, Answer
from app.security import hash_password, verify_password, create_token
import json

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="app/templates")

# Коды приглашений (можешь поменять на свои значения)
STUDENT_INVITE_CODE = "STUDENT2025"
TEACHER_INVITE_CODE = "TEACHER2025"


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


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

    # Первый пользователь в системе — всегда admin, без кода
    if not has_admin:
        role = "admin"
    else:
        # Для всех остальных обязателен код
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


# ---------- ADMIN: управление пользователями ----------

@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    users = db.query(User).order_by(User.id.asc()).all()
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "error": None,
            "success": None,
        },
    )


@router.post("/admin/users/update-role", response_class=HTMLResponse)
async def admin_update_role(
    request: Request,
    email: str = Form(...),
    new_role: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    valid_roles = ["admin", "teacher", "student"]
    if new_role not in valid_roles:
        users = db.query(User).order_by(User.id.asc()).all()
        return templates.TemplateResponse(
            "admin_users.html",
            {
                "request": request,
                "user": current_user,
                "users": users,
                "error": "Недопустимая роль",
                "success": None,
            },
            status_code=400,
        )

    target = db.query(User).filter(User.email == email.strip()).first()
    if not target:
        users = db.query(User).order_by(User.id.asc()).all()
        return templates.TemplateResponse(
            "admin_users.html",
            {
                "request": request,
                "user": current_user,
                "users": users,
                "error": "Пользователь с такой почтой не найден",
                "success": None,
            },
            status_code=404,
        )

    if target.id == current_user.id and new_role != "admin":
        users = db.query(User).order_by(User.id.asc()).all()
        return templates.TemplateResponse(
            "admin_users.html",
            {
                "request": request,
                "user": current_user,
                "users": users,
                "error": "Нельзя снять роль admin сам с себя",
                "success": None,
            },
            status_code=400,
        )

    target.role = new_role
    db.add(target)
    db.commit()

    users = db.query(User).order_by(User.id.asc()).all()
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "user": current_user,
            "users": users,
            "error": None,
            "success": f"Роль пользователя {target.email} изменена на {new_role}",
        },
    )


# ---------- ЛИЧНЫЙ КАБИНЕТ ----------

def build_profile_context(db: Session, user: User) -> dict:
    # Результаты самого пользователя
    subs = (
        db.query(Submission)
        .options(joinedload(Submission.test))
        .filter(Submission.user_id == user.id)
        .order_by(Submission.created_at.desc())
        .all()
    )
    student_results = []
    if subs:
        test_ids = {s.test_id for s in subs}
        tqs = (
            db.query(TestQuestion)
            .filter(TestQuestion.test_id.in_(test_ids))
            .all()
        )
        max_points_map: dict[int, int] = {}
        for tq in tqs:
            max_points_map.setdefault(tq.test_id, 0)
            max_points_map[tq.test_id] += tq.points
    else:
        max_points_map = {}

    for s in subs:
        t = s.test
        student_results.append(
            {
                "test_title": t.title if t else f"Тест #{s.test_id}",
                "score": s.score,
                "max_points": max_points_map.get(s.test_id, 0),
                "created_at": s.created_at,
            }
        )

    # Результаты учеников учителя / админа (по его тестам)
    teacher_rows = []
    if user.role in ("teacher", "admin"):
        tests = (
            db.query(Test)
            .options(
                joinedload(Test.submissions).joinedload(Submission.user)
            )
            .filter(Test.owner_id == user.id)
            .all()
        )
        if tests:
            test_ids2 = [t.id for t in tests]
            tqs2 = (
                db.query(TestQuestion)
                .filter(TestQuestion.test_id.in_(test_ids2))
                .all()
            )
            teacher_max: dict[int, int] = {}
            for tq in tqs2:
                teacher_max.setdefault(tq.test_id, 0)
                teacher_max[tq.test_id] += tq.points
        else:
            teacher_max = {}

        for t in tests:
            max_pts = teacher_max.get(t.id, 0)
            for s in t.submissions:
                student_email = s.user.email if s.user else f"ID {s.user_id}"
                teacher_rows.append(
                    {
                        "test_title": t.title,
                        "student_email": student_email,
                        "score": s.score,
                        "max_points": max_pts,
                        "created_at": s.created_at,
                    }
                )

        teacher_rows.sort(key=lambda r: r["created_at"], reverse=True)

    return {
        "student_results": student_results,
        "teacher_rows": teacher_rows,
    }


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx = build_profile_context(db, user)
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": user,
            "password_error": None,
            "password_success": None,
            **ctx,
        },
    )


@router.post("/profile/password", response_class=HTMLResponse)
async def profile_change_password(
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
        error = "Текущий пароль указан неверно."
    elif len(new_password) < 6:
        error = "Новый пароль должен быть не короче 6 символов."
    elif new_password != new_password2:
        error = "Новый пароль и подтверждение не совпадают."
    else:
        user.password_hash = hash_password(new_password)
        db.add(user)
        db.commit()
        success = "Пароль успешно изменён."

    ctx = build_profile_context(db, user)
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": user,
            "password_error": error,
            "password_success": success,
            **ctx,
        },
        status_code=400 if error else 200,
    )


# ---------- QUESTIONS UI ----------

@router.get("/questions", response_class=HTMLResponse)
async def questions_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows: List[Question] = db.query(Question).order_by(Question.id.desc()).all()
    return templates.TemplateResponse(
        "questions_list.html",
        {"request": request, "user": user, "questions": rows},
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

    if answer_type not in ("text", "single"):
        error = "Неверный тип ответа"
    elif answer_type == "text" and not correct_text.strip():
        error = "Укажите правильный текстовый ответ"
    elif answer_type == "single":
        options = []
        for idx in range(4):
            val = form.get(f"option_{idx}", "").strip()
            if val:
                options.append(val)
        if not options:
            error = "Укажите хотя бы один вариант ответа"
        elif correct_index == "":
            error = "Выберите, какой вариант считать правильным"

    if error:
        return templates.TemplateResponse(
            "question_new.html",
            {"request": request, "user": user, "error": error, "success": None},
            status_code=400,
        )

    if answer_type == "text":
        options_json = None
        correct = correct_text.strip()
    else:
        raw_options = []
        for idx in range(4):
            val = form.get(f"option_{idx}", "").strip()
            if val:
                raw_options.append(val)
        options_json = json.dumps(raw_options, ensure_ascii=False)
        correct = correct_index

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
    t = Test(title=title.strip(), owner_id=user.id)
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

    tqs: List[TestQuestion] = db.query(TestQuestion).filter(TestQuestion.test_id == test_id).all()
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

    tqs: List[TestQuestion] = db.query(TestQuestion).filter(TestQuestion.test_id == test_id).all()

    items = []
    max_points = 0
    score = 0

    for tq in tqs:
        q = db.get(Question, tq.question_id)
        if not q:
            continue

        field_name = f"answer_{q.id}"
        given = form.get(field_name, "").strip()
        opts = json.loads(q.options) if q.options else None
        max_points += tq.points

        if not given:
            correct_flag = 0
            earned = 0
        else:
            if q.answer_type == "text":
                gt = q.correct.strip().lower()
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
            given=given,
            correct=bool(correct_flag),
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
