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
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List, Optional
import json
import io
import zipfile
import re
import json
from pathlib import Path
from uuid import uuid4
import os

from app.deps import get_db, get_current_user, require_role, require_teacher_or_admin
from app.models import User, Question, Test, TestQuestion, Submission, Answer
from app.security import hash_password, verify_password, create_token
from app.models import AnswerOption
from sqlalchemy.orm import Session

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="app/templates")

STUDENT_INVITE_CODE = "STUDENT2025"
TEACHER_INVITE_CODE = "TEACHER2025"
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


# ---------- UPLOADS ----------


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
    if not file or not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Можно загружать только изображения")

    ext = Path(file.filename or "").suffix.lower()
    if not ext:
        ext = ".img"
    filename = f"{uuid4().hex}{ext}"
    dest = UPLOAD_DIR / filename

    data = await file.read()
    dest.write_bytes(data)

    url = f"/static/uploads/{filename}"
    return JSONResponse({"url": url})


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
    return redirect("/ui/account")


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
    Понимает файлы вида:

    **КЛАСС** #класс_9
    **ГОД** #год_1819
    **ЭТАП** #муницип
    **КАТЕГОРИЯ** [[#Дерево]]
    **ТИП ВОПРОСА** #закрытый/#открытый
    ...

    # Вопрос
    1) Текст вопроса...
    а) вариант 1;
    б) вариант 2;
    в) вариант 3;

    # Ответ
    б) вариант 2;

    Пустые шаблоны (нет текста вопроса и нет ответа) и заметки без "# Вопрос" — пропускаем.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return None

    # если вообще нет "# Вопрос" – это не задача
    if not re.search(r"^\s*#\s*Вопрос\b", text, re.IGNORECASE | re.MULTILINE):
        return None

    lines = text.split("\n")

    # --- ищем строку "# Вопрос" ---
    q_header_idx = None
    for i, line in enumerate(lines):
        if re.search(r"^\s*#\s*Вопрос\b", line, re.IGNORECASE):
            q_header_idx = i
            break
    if q_header_idx is None:
        return None

    question_start = q_header_idx + 1

    # ---------- МЕТАДАННЫЕ из шапки ----------
    meta_category: Optional[str] = None
    meta_grade: Optional[int] = None
    meta_year: Optional[str] = None
    meta_stage: Optional[str] = None
    meta_type: Optional[str] = None  # 'open' / 'closed'

    header_lines = lines[:q_header_idx]

    for meta_line in header_lines:
        # КЛАСС
        m = re.match(r"\s*\*\*\s*КЛАСС\s*\*\*\s*(.+)", meta_line, re.IGNORECASE)
        if m and meta_grade is None:
            val = m.group(1)
            m2 = re.search(r"(\d+)", val)
            if m2:
                try:
                    meta_grade = int(m2.group(1))
                except ValueError:
                    pass

        # ГОД
        m = re.match(r"\s*\*\*\s*ГОД\s*\*\*\s*(.+)", meta_line, re.IGNORECASE)
        if m and meta_year is None:
            val = m.group(1)
            m2 = re.search(r"(\d+)", val)
            if m2:
                meta_year = m2.group(1)

        # ЭТАП
        m = re.match(r"\s*\*\*\s*ЭТАП\s*\*\*\s*(.+)", meta_line, re.IGNORECASE)
        if m and meta_stage is None:
            val = m.group(1).strip()
            val = re.sub(r"^#+", "", val)  # "#муницип" -> "муницип"
            meta_stage = val or None

        # КАТЕГОРИЯ
        m = re.match(r"\s*\*\*\s*КАТЕГОРИЯ\s*\*\*\s*(.+)", meta_line, re.IGNORECASE)
        if m and meta_category is None:
            val = m.group(1).strip()
            # "[[#Дерево]]" -> "Дерево"
            val = re.sub(r"^\[\[", "", val)
            val = re.sub(r"]]$", "", val)
            val = val.lstrip("#").strip()
            meta_category = val or None

        # ТИП ВОПРОСА (#закрытый / #открытый)
        m = re.match(r"\s*\*\*\s*ТИП ВОПРОСА\s*\*\*\s*(.+)", meta_line, re.IGNORECASE)
        if m and meta_type is None:
            val = m.group(1).strip().lower()
            if "закрытый" in val:
                meta_type = "closed"
            elif "открытый" in val:
                meta_type = "open"

    # ---------- границы вопроса и ответ ----------
    answer_value = ""
    question_end = len(lines)

    # 1) "Ответ: ..." в одной строке
    inline_idx = None
    for idx, line in enumerate(lines):
        m = re.search(r"(Ответ|Answer)\s*[:\-]\s*(.+)", line, re.IGNORECASE)
        if m:
            inline_idx = idx
            answer_value = m.group(2).strip()
            question_end = idx
            break

    # 2) заголовок "# Ответ"
    if inline_idx is None:
        answer_header_idx = None
        for idx, line in enumerate(lines):
            if re.search(r"^\s*#\s*Ответ\b", line, re.IGNORECASE):
                answer_header_idx = idx
                break
        if answer_header_idx is not None:
            question_end = answer_header_idx
            for j in range(answer_header_idx + 1, len(lines)):
                candidate = lines[j].strip()
                if candidate:
                    answer_value = candidate
                    break

    question_lines = lines[question_start:question_end]

    # пустой вопрос + пустой ответ -> считаем шаблоном, не импортируем
    if (not any(l.strip() for l in question_lines)) and not answer_value.strip():
        return None

    # ---------- картинка ----------
    image_name = None
    m1 = re.search(r"!\[\[(.+?)\]\]", text)
    if m1:
        image_name = m1.group(1).strip()
    else:
        m2 = re.search(r"!\[[^\]]*]\((.+?)\)", text)
        if m2:
            image_name = m2.group(1).strip()

    # ---------- выбор одного варианта ----------
    choice_data = None
    if meta_type != "open":  # для открытых сразу идём в текстовый режим
        choice_data = _try_parse_choice(question_lines, answer_value)

    if choice_data:
        # choice_data уже содержит text / answer_type / options_json / correct
        choice_data["image_name"] = image_name
        choice_data["category"] = meta_category
        choice_data["grade"] = meta_grade
        choice_data["year"] = meta_year
        choice_data["stage"] = meta_stage
        return choice_data

    # ---------- fallback: текстовый ответ ----------
    question_text = "\n".join(question_lines).strip() or text
    question_text = re.sub(r"!\[\[.+?\]\]", "", question_text)
    question_text = re.sub(r"!\[[^\]]*]\(.+?\)", "", question_text)
    question_text = question_text.strip()

    return {
        "text": question_text,
        "answer_type": "text",
        "correct": answer_value.strip() if answer_value else "",
        "options_json": None,
        "image_name": image_name,
        "category": meta_category,
        "grade": meta_grade,
        "year": meta_year,
        "stage": meta_stage,
    }


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
            category=parsed.get("category"),
            grade=parsed.get("grade"),
            year=parsed.get("year"),
            stage=parsed.get("stage"),
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
    # забираем все вопросы
    rows: List[Question] = db.query(Question).all()

    # строим иерархию: Категория -> Класс -> Год -> Этап -> список вопросов
    library: dict[str, dict[int, dict[str, dict[str, list[Question]]]]] = {}

    for q in rows:
        category = q.category or "??? ?????????"
        try:
            grade = int(q.grade) if q.grade is not None else 0
        except (TypeError, ValueError):
            grade = 0
        year = q.year or "?"
        stage = q.stage or "?"

        library.setdefault(category, {}).setdefault(grade, {}).setdefault(year, {}).setdefault(stage, []).append(q)
    return templates.TemplateResponse(
        "questions_list.html",
        {
            "request": request,
            "user": user,
            "library": library,
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
    correct_number: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    form = await request.form()
    error: Optional[str] = None
    answer_type = answer_type.strip()

    def collect_options(form_data):
        entries = []
        for k in form_data.keys():
            m = re.match(r"option_(\d+)", k)
            if m:
                idx = int(m.group(1))
                entries.append((idx, (form_data.get(k) or "").strip()))
        entries.sort(key=lambda x: x[0])
        opts = [v for _, v in entries]
        while opts and not opts[-1]:
            opts.pop()
        return opts

    options = collect_options(form)

    if answer_type not in ("text", "single", "multi", "number"):
        error = "Неподдерживаемый тип ответа."
    elif answer_type == "text":
        if not correct_text.strip():
            error = "Укажите правильный текстовый ответ."
    elif answer_type in ("single", "multi"):
        if len(options) < 2:
            error = "Добавьте хотя бы два варианта ответа."
        elif answer_type == "single" and correct_index == "":
            error = "Отметьте правильный вариант."
        elif answer_type == "multi":
            try:
                correct_multi = [int(x) for x in form.getlist("correct_multi")]
            except Exception:
                correct_multi = []
            correct_multi = [i for i in correct_multi if 0 <= i < len(options)]
            if not correct_multi:
                error = "Отметьте хотя бы один правильный вариант."
    elif answer_type == "number":
        if not correct_number.strip():
            error = "Укажите правильное число."
        else:
            try:
                float(correct_number.strip())
            except ValueError:
                error = "Числовой ответ должен быть числом."

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

    options_json = None
    correct = None

    if answer_type == "text":
        correct = correct_text.strip()
    elif answer_type == "number":
        correct = correct_number.strip()
    elif answer_type == "multi":
        options_json = json.dumps(options, ensure_ascii=False) if options else None
        try:
            correct_multi = [int(x) for x in form.getlist("correct_multi")]
        except Exception:
            correct_multi = []
        correct_multi = [i for i in correct_multi if 0 <= i < len(options)]
        correct = json.dumps(sorted(set(correct_multi)), ensure_ascii=False)
    elif answer_type == "single":
        options_json = json.dumps(options, ensure_ascii=False) if options else None
        correct = str(correct_index)

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
            "success": f"Вопрос успешно сохранён. ID: {q.id}",
        },
    )

@router.get("/questions/{question_id}/edit", response_class=HTMLResponse)
async def question_edit(
    request: Request,
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin),
):
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    options = []
    correct_index = None
    correct_multi: list[int] = []
    correct_number = None

    if q.options:
        try:
            options = json.loads(q.options)
        except Exception:
            options = []
    if q.answer_type == "single" and q.correct is not None:
        try:
            correct_index = int(str(q.correct))
        except ValueError:
            correct_index = None
    if q.answer_type == "multi" and q.correct:
        try:
            correct_multi = json.loads(q.correct) if q.correct else []
        except Exception:
            correct_multi = []
    if q.answer_type == "number":
        correct_number = q.correct

    while len(options) < 4:
        options.append("")

    return templates.TemplateResponse(
        "question_edit.html",
        {
            "request": request,
            "user": user,
            "question": q,
            "options": options,
            "correct_index": correct_index,
            "correct_multi": correct_multi,
            "correct_number": correct_number,
            "error": None,
            "success": None,
        },
    )


@router.post("/questions/{question_id}/edit", response_class=HTMLResponse)
async def question_edit_post(
    request: Request,
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_teacher_or_admin),
):
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    form = await request.form()
    text = (form.get("text") or "").strip()
    answer_type = (form.get("answer_type") or "text").strip()

    def collect_options(form_data):
        entries = []
        for k in form_data.keys():
            m = re.match(r"option_(\d+)", k)
            if m:
                idx = int(m.group(1))
                entries.append((idx, (form_data.get(k) or "").strip()))
        entries.sort(key=lambda x: x[0])
        opts = [v for _, v in entries]
        while opts and not opts[-1]:
            opts.pop()
        return opts

    if not text:
        return templates.TemplateResponse(
            "question_edit.html",
            {
                "request": request,
                "user": user,
                "question": q,
                "options": collect_options(form),
                "correct_index": None,
                "correct_multi": [],
                "correct_number": None,
                "error": "Текст вопроса обязателен",
                "success": None,
            },
        )

    q.text = text
    q.answer_type = answer_type

    if answer_type == "text":
        q.correct = (form.get("correct_text") or "").strip()
        q.options = None
    elif answer_type == "number":
        num_raw = (form.get("correct_number") or "").strip()
        if not num_raw:
            return templates.TemplateResponse(
                "question_edit.html",
                {
                    "request": request,
                    "user": user,
                    "question": q,
                    "options": collect_options(form),
                    "correct_index": None,
                    "correct_multi": [],
                    "correct_number": None,
                    "error": "Укажите правильное число",
                    "success": None,
                },
                status_code=400,
            )
        try:
            float(num_raw)
        except ValueError:
            return templates.TemplateResponse(
                "question_edit.html",
                {
                    "request": request,
                    "user": user,
                    "question": q,
                    "options": collect_options(form),
                    "correct_index": None,
                    "correct_multi": [],
                    "correct_number": None,
                    "error": "Числовой ответ должен быть числом",
                    "success": None,
                },
                status_code=400,
            )
        q.correct = num_raw
        q.options = None
    elif answer_type in ("single", "multi"):
        options = collect_options(form)
        if len(options) < 2:
            return templates.TemplateResponse(
                "question_edit.html",
                {
                    "request": request,
                    "user": user,
                    "question": q,
                    "options": options,
                    "correct_index": None,
                    "correct_multi": [],
                    "correct_number": None,
                    "error": "Добавьте хотя бы два варианта ответа",
                    "success": None,
                },
                status_code=400,
            )
        q.options = json.dumps(options, ensure_ascii=False) if options else None
        if answer_type == "single":
            correct_raw = form.get("correct_index")
            if correct_raw is None or correct_raw == "":
                return templates.TemplateResponse(
                    "question_edit.html",
                    {
                        "request": request,
                        "user": user,
                        "question": q,
                        "options": options,
                        "correct_index": None,
                        "correct_multi": [],
                        "correct_number": None,
                        "error": "Отметьте правильный вариант",
                        "success": None,
                    },
                    status_code=400,
                )
            q.correct = str(correct_raw)
        else:
            try:
                correct_multi = [int(x) for x in form.getlist("correct_multi")]
            except Exception:
                correct_multi = []
            correct_multi = [i for i in correct_multi if 0 <= i < len(options)]
            if not correct_multi:
                return templates.TemplateResponse(
                    "question_edit.html",
                    {
                        "request": request,
                        "user": user,
                        "question": q,
                        "options": options,
                        "correct_index": None,
                        "correct_multi": [],
                        "correct_number": None,
                        "error": "Отметьте хотя бы один правильный вариант",
                        "success": None,
                    },
                    status_code=400,
                )
            q.correct = json.dumps(sorted(set(correct_multi)), ensure_ascii=False)
    else:
        return templates.TemplateResponse(
            "question_edit.html",
            {
                "request": request,
                "user": user,
                "question": q,
                "options": [],
                "correct_index": None,
                "correct_multi": [],
                "correct_number": None,
                "error": "Неподдерживаемый тип ответа",
                "success": None,
            },
            status_code=400,
        )

    db.add(q)
    db.commit()
    db.refresh(q)

    options = []
    correct_index = None
    correct_multi = []
    correct_number = None
    if q.options:
        try:
            options = json.loads(q.options)
        except Exception:
            options = []
    if q.answer_type == "single" and q.correct is not None:
        try:
            correct_index = int(str(q.correct))
        except ValueError:
            correct_index = None
    if q.answer_type == "multi" and q.correct:
        try:
            correct_multi = json.loads(q.correct) if q.correct else []
        except Exception:
            correct_multi = []
    if q.answer_type == "number":
        correct_number = q.correct
    while len(options) < 4:
        options.append("")

    return templates.TemplateResponse(
        "question_edit.html",
        {
            "request": request,
            "user": user,
            "question": q,
            "options": options,
            "correct_index": correct_index,
            "correct_multi": correct_multi,
            "correct_number": correct_number,
            "error": None,
            "success": "Изменения сохранены",
        },
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
    info = []
    for t in tests:
        tqs: List[TestQuestion] = (
            db.query(TestQuestion)
            .filter(TestQuestion.test_id == t.id)
            .all()
        )
        info.append(
            {
                "test": t,
                "question_count": len(tqs),
                "max_score": sum(tq.points for tq in tqs) if tqs else 0,
            }
        )
    role = user.role if user else None
    return templates.TemplateResponse(
        "tests_list.html",
        {"request": request, "user": user, "tests": tests, "test_info": info, "role": role},
    )


@router.get("/tests/new", response_class=HTMLResponse)
async def test_builder_new(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    questions: List[Question] = db.query(Question).order_by(Question.id.asc()).all()
    library: dict[str, dict[int, dict[str, dict[str, list[Question]]]]] = {}
    for q in questions:
        category = q.category or "??? ?????????"
        try:
            grade = int(q.grade) if q.grade is not None else 0
        except (TypeError, ValueError):
            grade = 0
        year = q.year or ""
        stage = q.stage or ""
        library.setdefault(category, {}).setdefault(grade, {}).setdefault(year, {}).setdefault(stage, []).append(q)
    return templates.TemplateResponse(
        "test_builder.html",
        {
            "request": request,
            "user": user,
            "mode": "create",
            "test": None,
            "questions": questions,
            "library": library,
            "selected": {},
        },
    )


@router.post("/tests/new", response_class=HTMLResponse)
async def test_builder_new_post(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    form = await request.form()
    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip()
    show_correct = "show_correct_answers" in form
    try:
        max_attempts = int(form.get("max_attempts") or 0)
    except ValueError:
        max_attempts = 0
    if max_attempts < 0:
        max_attempts = 0

    questions: List[Question] = db.query(Question).order_by(Question.id.asc()).all()
    selection: list[tuple[int, int]] = []
    for q in questions:
        if form.get(f"q_{q.id}_include") is None:
            continue
        try:
            points = int(form.get(f"q_{q.id}_points") or 1)
        except ValueError:
            points = 1
        selection.append((q.id, max(points, 0)))

    if not title:
        error = "Укажите название теста"
    elif not selection:
        error = "Выберите хотя бы один вопрос"
    else:
        error = None

    if error:
        library: dict[str, dict[int, dict[str, dict[str, list[Question]]]]] = {}
        for q in questions:
            category = q.category or "??? ?????????"
            try:
                grade = int(q.grade) if q.grade is not None else 0
            except (TypeError, ValueError):
                grade = 0
            year = q.year or ""
            stage = q.stage or ""
            library.setdefault(category, {}).setdefault(grade, {}).setdefault(year, {}).setdefault(stage, []).append(q)
        return templates.TemplateResponse(
            "test_builder.html",
            {
                "request": request,
                "user": user,
                "mode": "create",
                "test": None,
                "questions": questions,
                "library": library,
                "selected": {},
                "max_attempts": max_attempts,
                "error": error,
            },
            status_code=400,
        )

    t = Test(
        title=title,
        description=description or None,
        show_answers_to_student=show_correct,
        created_by_id=user.id if hasattr(user, "id") else None,
        max_attempts=max_attempts or None,
    )
    db.add(t)
    db.flush()

    order = 0
    for question_id, points in selection:
        order += 1
        tq = TestQuestion(
            test_id=t.id,
            question_id=question_id,
            points=points,
            order=order,
        )
        db.add(tq)

    db.commit()
    return redirect("/ui/tests")


@router.get("/tests/{test_id}/edit", response_class=HTMLResponse)
async def test_builder_edit(
    test_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="test not found")

    questions: List[Question] = db.query(Question).order_by(Question.id.asc()).all()
    library: dict[str, dict[int, dict[str, dict[str, list[Question]]]]] = {}
    for q in questions:
        category = q.category or "??? ?????????"
        try:
            grade = int(q.grade) if q.grade is not None else 0
        except (TypeError, ValueError):
            grade = 0
        year = q.year or ""
        stage = q.stage or ""
        library.setdefault(category, {}).setdefault(grade, {}).setdefault(year, {}).setdefault(stage, []).append(q)
    tqs: List[TestQuestion] = (
        db.query(TestQuestion)
        .filter(TestQuestion.test_id == test.id)
        .all()
    )
    selected = {tq.question_id: tq for tq in tqs}

    return templates.TemplateResponse(
        "test_builder.html",
        {
            "request": request,
            "user": user,
            "mode": "edit",
            "test": test,
            "questions": questions,
            "library": library,
            "selected": selected,
        },
    )


@router.post("/tests/{test_id}/edit", response_class=HTMLResponse)
async def test_builder_edit_post(
    test_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="test not found")

    form = await request.form()
    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip()
    show_correct = "show_correct_answers" in form
    try:
        max_attempts = int(form.get("max_attempts") or 0)
    except ValueError:
        max_attempts = 0
    if max_attempts < 0:
        max_attempts = 0

    questions: List[Question] = db.query(Question).order_by(Question.id.asc()).all()
    selection: list[tuple[int, int]] = []
    for q in questions:
        if form.get(f"q_{q.id}_include") is None:
            continue
        try:
            points = int(form.get(f"q_{q.id}_points") or 1)
        except ValueError:
            points = 1
        selection.append((q.id, max(points, 0)))

    if not title:
        error = "Укажите название теста"
    elif not selection:
        error = "Выберите хотя бы один вопрос"
    else:
        error = None

    if error:
        tqs: List[TestQuestion] = (
            db.query(TestQuestion)
            .filter(TestQuestion.test_id == test.id)
            .all()
        )
        selected = {tq.question_id: tq for tq in tqs}
        library: dict[str, dict[int, dict[str, dict[str, list[Question]]]]] = {}
        for q in questions:
            category = q.category or "??? ?????????"
            try:
                grade = int(q.grade) if q.grade is not None else 0
            except (TypeError, ValueError):
                grade = 0
            year = q.year or ""
            stage = q.stage or ""
            library.setdefault(category, {}).setdefault(grade, {}).setdefault(year, {}).setdefault(stage, []).append(q)
        return templates.TemplateResponse(
            "test_builder.html",
            {
                "request": request,
                "user": user,
                "mode": "edit",
                "test": test,
                "questions": questions,
                "library": library,
                "selected": selected,
                "max_attempts": max_attempts,
                "error": error,
            },
            status_code=400,
        )

    test.title = title
    test.description = description or None
    test.show_answers_to_student = show_correct
    test.max_attempts = max_attempts or None
    db.add(test)

    db.query(TestQuestion).filter(TestQuestion.test_id == test.id).delete()

    order = 0
    for question_id, points in selection:
        order += 1
        tq = TestQuestion(
            test_id=test.id,
            question_id=question_id,
            points=points,
            order=order,
        )
        db.add(tq)

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
        items.append({"tq": tq, "q": q, "options": opts, "given": None, "earned": 0})

    if not items:
        raise HTTPException(status_code=400, detail="test has no questions")

    # Render first question for the run page
    current = items[0]
    question = current["q"]
    answers_list = []
    if question.answer_type != "text":
        # prefer structured options if present
        if current["options"]:
            answers_list = [
                type("Opt", (), {"id": idx, "text": opt})
                for idx, opt in enumerate(current["options"])
            ]
        elif hasattr(question, "answers") and question.answers:
            answers_list = question.answers

    submission = None
    if submission_id is not None:
        submission = db.get(Submission, submission_id)
        if not submission or submission.user_id != user.id:
            submission = None

    # простая замена markdown изображений на <img>
    def md_to_html(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"!\\[[^\\]]*]\\(([^)]+)\\)", r'<img src="\\1" style="max-width:100%;height:auto;">', text)
        text = text.replace("\n", "<br>")
        return text

    return templates.TemplateResponse(
        "test_run.html",
        {
            "request": request,
            "user": user,
            "test": test,
            "items": items,
            "question": question,
            "question_html": md_to_html(question.text if hasattr(question, "text") else ""),
            "answers": answers_list,
            "answers_html": [md_to_html(getattr(a, "text", str(a))) for a in answers_list] if answers_list else None,
            "index": 0,
            "total_questions": len(items),
            "state_json": "",
            "selected_answer_id": None,
            "selected_answer_ids": [],
            "answer_text": "",
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


@router.get("/tests/{test_id}/start")
async def test_start_get(
    test_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Allow starting a test via GET (link click) by delegating to the POST handler.
    return await test_start(test_id=test_id, db=db, user=user)


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

    if test.show_answers_to_student:
        rows = []
        for idx, item in enumerate(items, 1):
            q = item["q"]
            opts = item["options"]
            given_raw = item.get("given") or ""

            your_answer = given_raw or "—"
            correct_answer = ""

            if q.answer_type == "text":
                correct_answer = (q.correct or "").strip()
            elif q.answer_type == "single":
                # map numeric index to option text
                try:
                    c_idx = int(q.correct) if q.correct is not None else None
                except (TypeError, ValueError):
                    c_idx = None
                if c_idx is not None and opts and 0 <= c_idx < len(opts):
                    correct_answer = str(opts[c_idx])
                else:
                    correct_answer = str(q.correct or "")

                try:
                    g_idx = int(given_raw)
                    if opts and 0 <= g_idx < len(opts):
                        your_answer = str(opts[g_idx])
                except (TypeError, ValueError):
                    pass

            rows.append(
                {
                    "index": idx,
                    "question": q,
                    "your_answer": your_answer,
                    "correct_answer": correct_answer,
                    "score": item.get("earned", 0),
                    "max_points": next((tq.points for tq in tqs if tq.question_id == q.id), 0),
                }
            )

        return templates.TemplateResponse(
            "test_result.html",
            {
                "request": request,
                "user": user,
                "test": test,
                "rows": rows,
                "total_score": score,
                "max_total": max_points,
            },
        )

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


@router.get("/submissions/{submission_id}", response_class=HTMLResponse)
async def submission_detail(
    submission_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    sub = db.get(Submission, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="submission not found")

    test = db.get(Test, sub.test_id)
    if not test:
        raise HTTPException(status_code=404, detail="test not found")
    student = db.get(User, sub.user_id) if hasattr(sub, "user_id") else None

    tqs: List[TestQuestion] = (
        db.query(TestQuestion)
        .filter(TestQuestion.test_id == test.id)
        .order_by(TestQuestion.order.asc())
        .all()
    )
    answers_map: dict[int, Answer] = {a.question_id: a for a in getattr(sub, "answers", [])}

    rows: List[dict] = []
    total_score = 0
    max_total = 0
    for idx, link in enumerate(tqs, 1):
        q = link.question or db.get(Question, link.question_id)
        if hasattr(q, "question") and not hasattr(q, "options"):
            if q.question is not None:
                q = q.question
        max_total += getattr(link, "points", 0) or 0

        ans = answers_map.get(q.id)
        given_raw = ""
        if ans:
            given_raw = getattr(ans, "answer_text", "") or getattr(ans, "given", "") or ""
        your_answer = given_raw or "—"
        correct_answer = ""
        recomputed_points = 0

        if q.answer_type == "text":
            correct_answer = (q.correct or "") if hasattr(q, "correct") else ""
            gt = (q.correct or "").strip().lower() if hasattr(q, "correct") else ""
            uv = (given_raw or "").strip().lower()
            if gt and uv and gt == uv:
                recomputed_points = getattr(link, "points", 0) or 0
        else:
            opts = []
            if q.options:
                try:
                    opts = json.loads(q.options)
                except Exception:
                    opts = []
            try:
                correct_idx = int(q.correct) if q.correct is not None else None
            except (TypeError, ValueError):
                correct_idx = None
            try:
                user_idx = int(getattr(ans, "selected_answer_id", None)) if ans else None
            except (TypeError, ValueError):
                user_idx = None
            if correct_idx is not None and opts and 0 <= correct_idx < len(opts):
                correct_answer = str(opts[correct_idx])
            else:
                correct_answer = str(q.correct or "")
            if user_idx is not None and opts and 0 <= user_idx < len(opts):
                your_answer = str(opts[user_idx])
            if correct_idx is not None and user_idx is not None and correct_idx == user_idx:
                recomputed_points = getattr(link, "points", 0) or 0

        display_points = getattr(ans, "points", None) if ans else None
        if display_points is None:
            display_points = recomputed_points
        total_score += display_points or 0

        rows.append(
            {
                "index": idx,
                "question": q,
                "your_answer": your_answer,
                "correct_answer": correct_answer,
                "score": display_points or 0,
                "max_points": getattr(link, "points", 0) or 0,
                "question_id": q.id,
                "answer_id": getattr(ans, "id", None) if ans else None,
            }
        )

    return templates.TemplateResponse(
        "test_result.html",
        {
            "request": request,
            "user": user,
            "test": test,
            "submission": sub,
            "student": student,
            "rows": rows,
            "total_score": total_score,
            "max_total": max_total,
            "can_edit": True,
        },
    )


@router.post("/submissions/{submission_id}/set-points")
async def submission_set_points(
    submission_id: int,
    question_id: int = Form(...),
    points: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher")),
):
    sub = db.get(Submission, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="submission not found")

    ans = (
        db.query(Answer)
        .filter(Answer.submission_id == submission_id, Answer.question_id == question_id)
        .first()
    )
    if not ans:
        raise HTTPException(status_code=404, detail="answer not found")

    ans.points = max(points, 0)
    db.add(ans)

    new_score = (
        db.query(Answer)
        .filter(Answer.submission_id == submission_id)
        .with_entities(Answer.points)
        .all()
    )
    sub.score = sum(p[0] or 0 for p in new_score)
    db.add(sub)
    db.commit()

    return RedirectResponse(url=f"/ui/submissions/{submission_id}", status_code=303)



