from fastapi import (
    APIRouter,
    Depends,
    Request,
    Form,
    status,
    HTTPException,
    UploadFile,
    File,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List, Optional
import json
import io
import zipfile
import re

from app.deps import get_db, get_current_user, require_role
from app.models import User, Question, Test, TestQuestion, Submission, Answer
from app.security import hash_password, verify_password, create_token

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="app/templates")

STUDENT_INVITE_CODE = "STUDENT2025"
TEACHER_INVITE_CODE = "TEACHER2025"


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


# ---------- ВСПОМОГАТЕЛЬНОЕ: контекст ЛК ----------


def build_account_context(
    request: Request,
    db: Session,
    user: User,
    password_error: Optional[str] = None,
    password_success: Optional[str] = None,
):
    student_results = None
    teacher_results = None

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
        max_points_map: dict[int, int] = {}

        for t in tests:
            tqs = (
                db.query(TestQuestion)
                .filter(TestQuestion.test_id == t.id)
                .all()
            )
            max_points_map[t.id] = sum(tq.points for tq in tqs) if tqs else 0

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


# ---------- ЛИЧНЫЙ КАБИНЕТ ----------


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


# ---------- IMPORT: ZIP с .md ----------

def _try_parse_choice(
    question_lines: List[str],
    answer_line: str,
) -> Optional[dict]:
    """
    Пытаемся выделить варианты вида "а) текст", "б) текст" и
    понять, какой из них правильный по строке ответа.
    """
    opt_re = re.compile(r"^\s*([A-Za-zА-Яа-я])\)\s*(.+)")
    options: List[str] = []
    letter_to_index: dict[str, int] = {}
    first_opt_idx: Optional[int] = None

    for i, line in enumerate(question_lines):
        m = opt_re.match(line)
        if not m:
            continue
        letter = m.group(1).lower()
        body = m.group(2).strip()
        options.append(body)
        letter_to_index[letter] = len(options) - 1
        if first_opt_idx is None:
            first_opt_idx = i

    if len(options) < 2:
        return None

    # Ищем букву/текст в строке ответа
    ans = answer_line.strip()
    ans_letter: Optional[str] = None
    ans_clean = ""

    m_ans = opt_re.match(ans)
    if m_ans:
        ans_letter = m_ans.group(1).lower()
        ans_clean = m_ans.group(2).strip(" .;").lower()
    else:
        m_letter_only = re.search(r"([A-Za-zА-Яа-я])\)", ans)
        if m_letter_only:
            ans_letter = m_letter_only.group(1).lower()
            ans_clean = ans[m_letter_only.end() :].strip(" .;").lower()
        else:
            ans_clean = ans.strip(" .;").lower()

    correct_index: Optional[int] = None

    # Сначала пробуем по букве
    if ans_letter and ans_letter in letter_to_index:
        correct_index = letter_to_index[ans_letter]
    else:
        normalized_ans = ans_clean
        for idx, opt in enumerate(options):
            opt_norm = opt.strip(" .;").lower()
            if opt_norm and opt_norm == normalized_ans:
                correct_index = idx
                break

    if correct_index is None:
        return None

    # Текст вопроса — всё до первой строки с вариантом
    if first_opt_idx is None:
        first_opt_idx = 0
    q_lines = question_lines[:first_opt_idx]
    # обрезаем пустые
    while q_lines and not q_lines[0].strip():
        q_lines.pop(0)
    while q_lines and not q_lines[-1].strip():
        q_lines.pop()
    question_text = "\n".join(q_lines).strip()
    if not question_text:
        # на всякий случай
        question_text = "\n".join(question_lines).strip()

    return {
        "text": question_text,
        "answer_type": "single",
        "options_json": json.dumps(options, ensure_ascii=False),
        "correct": str(correct_index),
    }


def parse_markdown_to_question(raw: str) -> Optional[dict]:
    """
    Понимает два варианта:

    1) где ответ в одной строке: "Ответ: ...";
    2) структура с заголовками "# Вопрос" и "# Ответ", как в Obsidian:
       текст вопроса + варианты, ниже блок "# Ответ" с правильным вариантом. :contentReference[oaicite:1]{index=1}
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return None

    lines = text.split("\n")

    # ===== Вариант 2: "# Вопрос" / "# Ответ" =====
    q_idx = None
    a_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        heading = stripped.lstrip("#").strip()
        if q_idx is None and re.match(r"^(Вопрос|Question)\b", heading, re.IGNORECASE):
            q_idx = i
        elif a_idx is None and re.match(r"^(Ответ|Answer)\b", heading, re.IGNORECASE):
            a_idx = i

    if q_idx is not None and a_idx is not None and a_idx > q_idx:
        q_lines = lines[q_idx + 1 : a_idx]

        # обрезаем пустые
        while q_lines and not q_lines[0].strip():
            q_lines.pop(0)
        while q_lines and not q_lines[-1].strip():
            q_lines.pop()

        # строка ответа — первая непустая после "# Ответ"
        ans_line = ""
        for j in range(a_idx + 1, len(lines)):
            candidate = lines[j].strip()
            if candidate:
                ans_line = candidate
                break

        if ans_line:
            # пытаемся распарсить выбор одного варианта
            choice = _try_parse_choice(q_lines, ans_line)
            if choice:
                return choice

            # fallback: просто текстовый ответ
            question_text = "\n".join(q_lines).strip() or text
            return {
                "text": question_text,
                "answer_type": "text",
                "options_json": None,
                "correct": ans_line.strip(),
            }

    # ===== Вариант 1: "Ответ: ..." в одной строке =====
    for idx, line in enumerate(lines):
        m = re.search(r"(Ответ|Answer)\s*[:\-]\s*(.+)", line, re.IGNORECASE)
        if not m:
            continue
        ans = m.group(2).strip()
        if not ans:
            continue

        q_lines = lines[:idx]
        while q_lines and not q_lines[0].strip():
            q_lines.pop(0)
        while q_lines and not q_lines[-1].strip():
            q_lines.pop()

        # тоже пробуем выделить варианты из тела вопроса
        choice = _try_parse_choice(q_lines, ans)
        if choice:
            return choice

        question_text = "\n".join(q_lines).strip() or text
        return {
            "text": question_text,
            "answer_type": "text",
            "options_json": None,
            "correct": ans,
        }

    return None


@router.get("/import", response_class=HTMLResponse)
async def import_page(
    request: Request,
    user: User = Depends(require_role("admin", "teacher")),
):
    return templates.TemplateResponse(
        "import.html",
        {
            "request": request,
            "user": user,
            "error": None,
            "summary": None,
        },
    )


@router.post("/import", response_class=HTMLResponse)
async def import_submit(
    request: Request,
    archive: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    filename = archive.filename or ""
    if not filename.lower().endswith(".zip"):
        return templates.TemplateResponse(
            "import.html",
            {
                "request": request,
                "user": user,
                "error": "Ожидается .zip‑архив с .md файлами.",
                "summary": None,
            },
            status_code=400,
        )

    try:
        data = await archive.read()
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        return templates.TemplateResponse(
            "import.html",
            {
                "request": request,
                "user": user,
                "error": "Не удалось прочитать архив. Проверь, что это корректный .zip.",
                "summary": None,
            },
            status_code=400,
        )

    imported_count = 0
    skipped_count = 0
    created_questions: List[Question] = []

    for name in zf.namelist():
        if name.endswith("/") or not name.lower().endswith(".md"):
            continue

        try:
            raw_bytes = zf.read(name)
        except KeyError:
            continue

        try:
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = raw_bytes.decode("cp1251", errors="ignore")

        parsed = parse_markdown_to_question(raw_text)
        if not parsed:
            skipped_count += 1
            continue

        q = Question(
            text=parsed["text"],
            answer_type=parsed["answer_type"],
            options=parsed["options_json"],
            correct=parsed["correct"],
        )
        db.add(q)
        db.flush()
        created_questions.append(q)
        imported_count += 1

    db.commit()

    summary = {
        "filename": filename,
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "created_questions": created_questions,
    }

    return templates.TemplateResponse(
        "import.html",
        {
            "request": request,
            "user": user,
            "error": None,
            "summary": summary,
        },
    )


# ---------- QUESTIONS: список / новая / редактор / удаление ----------


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
        {
            "request": request,
            "user": user,
            "error": None,
            "success": None,
        },
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
    raw_options: List[str] = []

    if answer_type not in ("text", "single"):
        error = "Неверный тип ответа."
    elif answer_type == "text":
        if not correct_text.strip():
            error = "Укажите правильный текстовый ответ."
    elif answer_type == "single":
        for idx in range(4):
            val = form.get(f"option_{idx}", "").strip()
            if val:
                raw_options.append(val)
        if not raw_options:
            error = "Укажите хотя бы один вариант ответа."
        elif correct_index == "":
            error = "Выберите, какой вариант считать правильным."

    if error:
        return templates.TemplateResponse(
            "question_new.html",
            {
                "request": request,
                "user": user,
                "error": error,
                "success": None,
            },
            status_code=400,
        )

    if answer_type == "text":
        options_json = None
        correct = correct_text.strip()
    else:
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

    options: List[str] = []
    selected_correct: Optional[int] = None
    correct_text = ""

    if q.answer_type == "single":
        options = json.loads(q.options) if q.options else []
        try:
            selected_correct = int(q.correct)
        except Exception:
            selected_correct = None
    else:
        correct_text = q.correct or ""

    return templates.TemplateResponse(
        "question_edit.html",
        {
            "request": request,
            "user": user,
            "question": q,
            "options": options,
            "selected_correct": selected_correct,
            "correct_text": correct_text,
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
    success: Optional[str] = None
    raw_options: List[str] = []

    answer_type = answer_type.strip()

    if answer_type not in ("text", "single"):
        error = "Неверный тип ответа."
    elif answer_type == "text":
        if not correct_text.strip():
            error = "Укажите правильный текстовый ответ."
    elif answer_type == "single":
        for idx in range(4):
            val = form.get(f"option_{idx}", "").strip()
            if val:
                raw_options.append(val)
        if not raw_options:
            error = "Укажите хотя бы один вариант ответа."
        elif correct_index == "":
            error = "Выберите, какой вариант считать правильным."

    if not error:
        if answer_type == "text":
            q.text = text
            q.answer_type = "text"
            q.options = None
            q.correct = correct_text.strip()
        else:
            q.text = text
            q.answer_type = "single"
            q.options = json.dumps(raw_options, ensure_ascii=False)
            q.correct = correct_index

        db.add(q)
        db.commit()
        success = "Задача успешно обновлена."

    options: List[str] = []
    selected_correct: Optional[int] = None
    correct_text_val = ""

    if q.answer_type == "single":
        options = json.loads(q.options) if q.options else []
        try:
            selected_correct = int(q.correct)
        except Exception:
            selected_correct = None
    else:
        correct_text_val = q.correct or ""

    return templates.TemplateResponse(
        "question_edit.html",
        {
            "request": request,
            "user": user,
            "question": q,
            "options": options,
            "selected_correct": selected_correct,
            "correct_text": correct_text_val,
            "error": error,
            "success": success,
        },
        status_code=400 if error else 200,
    )


@router.post("/questions/{question_id}/delete", response_class=HTMLResponse)
async def question_delete(
    question_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    q = db.get(Question, question_id)
    if not q:
        error = f"Вопрос #{question_id} не найден."
        rows: List[Question] = db.query(Question).order_by(Question.id.desc()).all()
        return templates.TemplateResponse(
            "questions_list.html",
            {
                "request": request,
                "user": user,
                "questions": rows,
                "error": error,
                "success": None,
            },
            status_code=400,
        )

    db.query(Answer).filter(Answer.question_id == question_id).delete()
    db.query(TestQuestion).filter(TestQuestion.question_id == question_id).delete()
    db.delete(q)
    db.commit()

    rows: List[Question] = db.query(Question).order_by(Question.id.desc()).all()
    return templates.TemplateResponse(
        "questions_list.html",
        {
            "request": request,
            "user": user,
            "questions": rows,
            "error": None,
            "success": f"Вопрос #{question_id} удалён.",
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

        given = form.get(field_name, "").strip()
        if not given:
            correct_flag = 0
            earned = 0
        else:
            if q.answer_type == "text":
                gt = (q.correct or "").strip().lower()
                uv = given.strip().lower()
                ok = gt == uv
                correct_flag = 1 if ok else 0
                earned = tq.points if ok else 0
            elif q.answer_type == "single":
                ok = q.correct == given
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
