from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_role
from app.models import (
    Test, TestQuestion, Question, Submission, Answer, User
)
from app.schemas import (
    TestCreate, TestOut, AddQuestionToTest,
    StartSubmission, AnswerIn
)

router = APIRouter(prefix="/tests", tags=["tests"])


@router.post("", response_model=TestOut)
def create_test(
    payload: TestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher"))
):
    t = Test(title=payload.title)
    db.add(t)
    db.commit()
    db.refresh(t)
    return TestOut(id=t.id, title=t.title)


@router.post("/{test_id}/add-question")
def add_question_to_test(
    test_id: int,
    payload: AddQuestionToTest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "teacher"))
):
    t = db.get(Test, test_id)
    q = db.get(Question, payload.question_id)
    if not t:
        raise HTTPException(404, "test not found")
    if not q:
        raise HTTPException(404, "question not found")

    tq = TestQuestion(
        test_id=test_id,
        question_id=payload.question_id,
        points=payload.points
    )
    db.add(tq)
    db.commit()
    return {"ok": True}


@router.post("/start")
def start_submission(
    payload: StartSubmission,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    s = Submission(test_id=payload.test_id, user_id=user.id, score=0)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"submission_id": s.id}


def _grade(question: Question, given: str, points: int):
    if question.answer_type == "text":
        ok = (question.correct.strip().lower() ==
              given.strip().lower())
        return int(ok), (points if ok else 0)

    if question.answer_type == "single":
        ok = (question.correct == given)
        return int(ok), (points if ok else 0)

    return 0, 0


@router.post("/answer")
def answer_question(
    payload: AnswerIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    submission = (
        db.query(Submission)
        .filter(Submission.user_id == user.id)
        .order_by(Submission.id.desc())
        .first()
    )
    if not submission:
        raise HTTPException(400, "no active submission")

    q = db.get(Question, payload.question_id)
    if not q:
        raise HTTPException(404, "question not found")

    tq = (
        db.query(TestQuestion)
        .filter(
            TestQuestion.test_id == submission.test_id,
            TestQuestion.question_id == q.id
        )
        .first()
    )
    if not tq:
        raise HTTPException(400, "question not in test")

    correct, earned = _grade(q, payload.given, tq.points)

    ans = (
        db.query(Answer)
        .filter(Answer.submission_id == submission.id, Answer.question_id == q.id)
        .first()
    )
    old_points = 0
    if ans:
        old_points = ans.points or 0
        ans.given = payload.given
        ans.correct = bool(correct)
        ans.points = earned
    else:
        ans = Answer(
            submission_id=submission.id,
            question_id=q.id,
            given=payload.given,
            correct=bool(correct),
            points=earned,
        )
        db.add(ans)

    submission.score = max((submission.score or 0) - old_points + earned, 0)
    db.add(submission)

    db.commit()
    return {"correct": bool(correct), "earned": earned, "score": submission.score}
