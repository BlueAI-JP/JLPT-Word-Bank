import asyncio
import os
import secrets
import smtplib
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from time import monotonic
from typing import Annotated, Optional

PRODUCTION = os.getenv("PRODUCTION", "false").lower() == "true"
PORT = int(os.getenv("PORT", "8000"))
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import database as db
from data_loader import WordDataLoader

# ---------------------------------------------------------------------------
# App startup / lifespan
# ---------------------------------------------------------------------------

loader = WordDataLoader()

# In-memory session store: token -> user_id
_sessions: dict[str, int] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    loader.load_all()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Anti-scraping middleware (rate limit on word/audio endpoints)
# ---------------------------------------------------------------------------

_ip_timestamps: dict[str, list[float]] = defaultdict(list)
RATE_WINDOW   = 60    # seconds
RATE_MAX_REQS = 300   # max requests per IP per window on protected paths
_PROTECTED    = ("/api/audio/", "/api/words/")


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if any(path.startswith(p) for p in _PROTECTED):
        ip  = _get_client_ip(request)
        now = monotonic()
        ts  = _ip_timestamps[ip]
        cutoff = now - RATE_WINDOW
        # Prune old entries
        _ip_timestamps[ip] = [t for t in ts if t > cutoff]
        _ip_timestamps[ip].append(now)
        if len(_ip_timestamps[ip]) > RATE_MAX_REQS:
            return PlainTextResponse("Too Many Requests", status_code=429)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_session(session_token: Optional[str]) -> int:
    if not session_token or session_token not in _sessions:
        raise HTTPException(status_code=401, detail="未登入")
    return _sessions[session_token]


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    return request.client.host


async def _require_admin(session_token: Optional[str]) -> int:
    """Validate session and confirm the user is in admin_emails. Returns user_id."""
    user_id = _require_session(session_token)
    user = await db.get_user_by_id(user_id)
    if not user or not await db.is_admin(user.get("email")):
        raise HTTPException(status_code=403, detail="需要管理者權限")
    return user_id


async def _is_effective_vip(user: dict) -> bool:
    """管理者自動擁有 VIP 權限；或 DB 中 is_vip 為 True。"""
    if user.get("is_vip"):
        return True
    return await db.is_admin(user.get("email"))


async def _require_vip(session_token: Optional[str]) -> int:
    """Validate session and confirm the user is VIP (or admin). Returns user_id."""
    user_id = _require_session(session_token)
    user = await db.get_user_by_id(user_id)
    if not user or not await _is_effective_vip(user):
        raise HTTPException(status_code=403, detail="此功能僅限 VIP 使用者")
    return user_id


def _make_session(response: Response, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = user_id
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="strict",
        secure=PRODUCTION,
        max_age=86400 * 7,
    )
    return token


# ---------------------------------------------------------------------------
# Email notification
# ---------------------------------------------------------------------------

def _send_email_sync(to_emails: list[str], subject: str, body: str) -> None:
    """Send email via Gmail SMTP (blocking — call via asyncio.to_thread)."""
    msg = MIMEMultipart()
    msg["From"]    = db.DEFAULT_ADMIN
    msg["To"]      = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(db.DEFAULT_ADMIN, GMAIL_APP_PASSWORD)
        smtp.sendmail(db.DEFAULT_ADMIN, to_emails, msg.as_string())


async def _notify_new_user(user_info: dict, ip: str) -> None:
    """Fire-and-forget: email all admins about a new user registration."""
    if not GMAIL_APP_PASSWORD:
        return
    try:
        admin_emails = await db.get_admin_emails()
        if not admin_emails:
            return
        email_addr = user_info.get("email", "（未知）")
        subject = f"[單字王通知] - 新使用者登入 - {email_addr}"
        body = (
            f"新使用者登入通知\n"
            f"{'=' * 40}\n"
            f"Email     : {email_addr}\n"
            f"姓名      : {user_info.get('name', '')}\n"
            f"Google ID : {user_info.get('sub', '')}\n"
            f"頭像 URL  : {user_info.get('picture', '')}\n"
            f"登入 IP   : {ip}\n"
            f"登入時間  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        await asyncio.to_thread(_send_email_sync, admin_emails, subject, body)
    except Exception:
        pass  # Email failure must never block the login flow


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    name: str


class MasterWordRequest(BaseModel):
    user_id: int
    level: str
    word_id: int


class StudyCompleteRequest(BaseModel):
    user_id: int
    level: str


class QuizCompleteRequest(BaseModel):
    user_id: int
    level: str
    score: float
    wrong_word_ids: list[int] = []


class AdminAddEmailRequest(BaseModel):
    email: str


class BanUserRequest(BaseModel):
    reason: str = ""


class BookProgressRequest(BaseModel):
    queue: list[int]


# ---------------------------------------------------------------------------
# Routes - Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return (
        "User-agent: *\n"
        "Disallow: /api/\n"
        "Disallow: /auth/\n"
    )


# ---------------------------------------------------------------------------
# Routes - Google SSO
# ---------------------------------------------------------------------------

@app.get("/auth/google")
async def auth_google():
    if not auth.GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google OAuth 尚未設定（缺少 GOOGLE_CLIENT_ID）")
    url = auth.build_auth_url()
    return RedirectResponse(url)


@app.get("/auth/google/callback")
async def auth_google_callback(
    request: Request,
    response: Response,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error:
        return RedirectResponse("/?auth_error=" + error)
    if not code or not state:
        raise HTTPException(400, "缺少 OAuth 必要參數")
    if not auth.validate_state(state):
        raise HTTPException(400, "無效的 OAuth state，請重新登入")

    try:
        user_info = await auth.fetch_google_user(code)
    except Exception as e:
        raise HTTPException(502, f"Google 驗證失敗：{e}")

    ip = _get_client_ip(request)
    user_id, is_new = await db.get_or_create_google_user(
        google_id=user_info["sub"],
        name=user_info.get("name", ""),
        email=user_info.get("email", ""),
        avatar_url=user_info.get("picture", ""),
        ip=ip,
    )

    if await db.is_banned(user_id):
        return RedirectResponse("/?auth_error=banned", status_code=302)

    # Non-blocking email notification for new registrations
    if is_new:
        asyncio.create_task(_notify_new_user(user_info, ip))

    redirect = RedirectResponse("/", status_code=302)
    _make_session(redirect, user_id)
    return redirect


# ---------------------------------------------------------------------------
# Routes - Auth (legacy + anonymous)
# ---------------------------------------------------------------------------

@app.get("/api/users")
async def list_users():
    return await db.get_all_users()


@app.post("/api/login")
async def login(req: LoginRequest, response: Response):
    """Legacy name-based login (kept for backward compat)."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名字不能為空")
    user_id = await db.get_or_create_user(name)
    token = _make_session(response, user_id)
    return {"user_id": user_id, "name": name, "token": token, "is_anonymous": False}


@app.post("/api/login/anonymous")
async def login_anonymous(request: Request, response: Response):
    user_id = await db.get_anonymous_user_id()
    ip = _get_client_ip(request)
    today = date.today().isoformat()
    usage = await db.get_anonymous_usage(ip, today)
    token = _make_session(response, user_id)
    return {
        "user_id": user_id,
        "name": "訪客",
        "is_anonymous": True,
        "token": token,
        "study_remaining": max(0, db.ANON_STUDY_LIMIT - usage["study_count"]),
        "quiz_remaining":  max(0, db.ANON_QUIZ_LIMIT  - usage["quiz_count"]),
    }


@app.post("/api/logout")
async def logout(
    response: Response,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    if session_token and session_token in _sessions:
        del _sessions[session_token]
    response.delete_cookie("session_token")
    return {"ok": True}


@app.get("/api/me")
async def get_me(
    request: Request,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    user_id = _require_session(session_token)
    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "使用者不存在")

    if not user["is_anonymous"] and await db.is_banned(user_id):
        if session_token and session_token in _sessions:
            del _sessions[session_token]
        raise HTTPException(403, "此帳號已被封禁，請聯繫管理員")

    result: dict = {
        "user_id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "avatar_url": user["avatar_url"],
        "is_anonymous": user["is_anonymous"],
        "is_admin": await db.is_admin(user.get("email")),
        "is_vip": await _is_effective_vip(user),
        "study_remaining": None,
        "quiz_remaining": None,
    }

    if user["is_anonymous"]:
        ip = _get_client_ip(request)
        today = date.today().isoformat()
        usage = await db.get_anonymous_usage(ip, today)
        result["name"] = "訪客"
        result["study_remaining"] = max(0, db.ANON_STUDY_LIMIT - usage["study_count"])
        result["quiz_remaining"]  = max(0, db.ANON_QUIZ_LIMIT  - usage["quiz_count"])

    return result


# ---------------------------------------------------------------------------
# Routes - Levels & Words
# ---------------------------------------------------------------------------

@app.get("/api/levels")
async def get_levels():
    return loader.get_all_levels()


@app.get("/api/words/{level}")
async def get_words(
    level: str,
    request: Request,
    user_id: Optional[int] = None,
    exclude_mastered: bool = False,
    mode: Optional[str] = None,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    uid = _require_session(session_token)
    words = loader.get_words(level)
    if not words:
        raise HTTPException(status_code=404, detail=f"等級 {level} 尚無單字資料")

    user = await db.get_user_by_id(uid)
    is_anon = user and user["is_anonymous"]

    if is_anon and mode in ("study", "quiz"):
        ip = _get_client_ip(request)
        today = date.today().isoformat()
        usage = await db.get_anonymous_usage(ip, today)

        if mode == "study" and usage["study_count"] >= db.ANON_STUDY_LIMIT:
            raise HTTPException(403, "今日試用學習次數已達上限，請登入 Google 帳號繼續使用")
        if mode == "quiz" and usage["quiz_count"] >= db.ANON_QUIZ_LIMIT:
            raise HTTPException(403, "今日試用測驗次數已達上限，請登入 Google 帳號繼續使用")

        await db.increment_anonymous_usage(ip, today, mode)
        return words

    if exclude_mastered and user_id is not None:
        mastered = await db.get_mastered_word_ids(user_id, level)
        words = [w for w in words if w["id"] not in mastered]

    return words


# ---------------------------------------------------------------------------
# Routes - Audio (protected, rate-limited by middleware)
# ---------------------------------------------------------------------------

@app.get("/api/audio/{level}/{word_id}")
async def get_audio(
    level: str,
    word_id: int,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    audio_path = loader.get_audio_path(level, word_id)
    if audio_path is None:
        raise HTTPException(status_code=404, detail="音檔不存在")

    def iter_file():
        with open(audio_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": "inline",
        },
    )


# ---------------------------------------------------------------------------
# Routes - Progress
# ---------------------------------------------------------------------------

@app.get("/api/progress/{user_id}")
async def get_progress(
    user_id: int,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    result = {}
    for level_info in loader.get_all_levels():
        level = level_info["level"]
        total = level_info["total"]
        mastered_ids = await db.get_mastered_word_ids(user_id, level)
        study_stats = await db.get_study_stats(user_id, level)
        quiz_stats = await db.get_quiz_stats(user_id, level)
        wrong_ids = await db.get_quiz_wrong_word_ids(user_id, level)
        result[level] = {
            "total": total,
            "mastered": len(mastered_ids),
            "available": level_info["available"],
            "study": study_stats,
            "quiz": quiz_stats,
            "wrong_count": len(wrong_ids),
        }
    return result


@app.post("/api/mastered")
async def mark_mastered(
    req: MasterWordRequest,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    await db.add_mastered_word(req.user_id, req.level, req.word_id)
    return {"ok": True}


@app.delete("/api/mastered/{user_id}/{level}")
async def reset_mastered(
    user_id: int,
    level: str,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    await db.reset_mastered_words(user_id, level)
    return {"ok": True}


@app.get("/api/mastered-words/{user_id}/{level}")
async def get_mastered_words(
    user_id: int,
    level: str,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    mastered_ids = await db.get_mastered_word_ids(user_id, level)
    all_words = loader.get_words(level)
    return [w for w in all_words if w["id"] in mastered_ids]


# ---------------------------------------------------------------------------
# Routes - Sessions
# ---------------------------------------------------------------------------

@app.post("/api/study/complete")
async def complete_study(
    req: StudyCompleteRequest,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    await db.add_study_session(req.user_id, req.level)
    return {"ok": True}


@app.delete("/api/study/reset/{user_id}/{level}")
async def reset_study(
    user_id: int,
    level: str,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    await db.reset_study_sessions(user_id, level)
    return {"ok": True}


@app.post("/api/quiz/complete")
async def complete_quiz(
    req: QuizCompleteRequest,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    await db.add_quiz_session(req.user_id, req.level, req.score)
    if req.wrong_word_ids:
        await db.add_quiz_wrong_words_batch(req.user_id, req.level, req.wrong_word_ids)
    return {"ok": True}


@app.delete("/api/quiz/reset/{user_id}/{level}")
async def reset_quiz(
    user_id: int,
    level: str,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    await db.reset_quiz_sessions(user_id, level)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Routes - Quiz Wrong Words
# ---------------------------------------------------------------------------

@app.get("/api/quiz/wrong-words/{user_id}/{level}")
async def get_wrong_words(
    user_id: int,
    level: str,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    uid = _require_session(session_token)
    user = await db.get_user_by_id(uid)
    if user and user["is_anonymous"]:
        raise HTTPException(403, "試用帳號無法使用測驗檢討功能，請登入 Google 帳號")
    wrong_ids = await db.get_quiz_wrong_word_ids(user_id, level)
    all_words = loader.get_words(level)
    return [w for w in all_words if w["id"] in wrong_ids]


@app.delete("/api/quiz/wrong-words/{user_id}/{level}/{word_id}")
async def remove_wrong_word(
    user_id: int,
    level: str,
    word_id: int,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    await db.remove_quiz_wrong_word(user_id, level, word_id)
    return {"ok": True}


@app.delete("/api/quiz/wrong-words/{user_id}/{level}")
async def reset_wrong_words(
    user_id: int,
    level: str,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    await db.reset_quiz_wrong_words(user_id, level)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Routes - Book Reading (VIP only)
# ---------------------------------------------------------------------------

@app.get("/api/book/{level}/progress")
async def get_book_progress(
    level: str,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    user_id = await _require_vip(session_token)
    progress = await db.get_book_progress(user_id, level)
    total = len(loader.get_words(level))
    if progress is None:
        return {"initialized": False, "queue": [], "total": total}
    return {"initialized": True, "queue": progress["queue"], "total": total}


@app.post("/api/book/{level}/progress")
async def save_book_progress(
    level: str,
    req: BookProgressRequest,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    user_id = await _require_vip(session_token)
    await db.save_book_progress(user_id, level, req.queue)
    return {"ok": True}


@app.delete("/api/book/{level}/progress")
async def reset_book_progress(
    level: str,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    user_id = await _require_vip(session_token)
    await db.reset_book_progress(user_id, level)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Routes - Admin
# ---------------------------------------------------------------------------

@app.get("/api/admin/users")
async def admin_list_users(
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    await _require_admin(session_token)
    return await db.get_all_users_admin()


@app.post("/api/admin/users/{user_id}/ban")
async def admin_ban_user(
    user_id: int,
    req: BanUserRequest,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    admin_uid = await _require_admin(session_token)
    admin_user = await db.get_user_by_id(admin_uid)
    if user_id == admin_uid:
        raise HTTPException(400, "不可封禁自己的帳號")
    await db.ban_user(user_id, banned_by=admin_user["email"] or "", reason=req.reason)
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}/ban")
async def admin_unban_user(
    user_id: int,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    await _require_admin(session_token)
    await db.unban_user(user_id)
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(
    user_id: int,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    admin_uid = await _require_admin(session_token)
    if user_id == admin_uid:
        raise HTTPException(400, "不可刪除自己的帳號")
    try:
        await db.delete_user(user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/vip")
async def admin_set_vip(
    user_id: int,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    await _require_admin(session_token)
    await db.set_user_vip(user_id, True)
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}/vip")
async def admin_unset_vip(
    user_id: int,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    await _require_admin(session_token)
    await db.set_user_vip(user_id, False)
    return {"ok": True}


@app.get("/api/admin/admins")
async def admin_list_admins(
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    await _require_admin(session_token)
    return {
        "emails": await db.get_admin_emails(),
        "default_admin": db.DEFAULT_ADMIN,
    }


@app.post("/api/admin/admins")
async def admin_add_admin_email(
    req: AdminAddEmailRequest,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    await _require_admin(session_token)
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "請輸入有效的 Email")
    await db.add_admin_email(email)
    return {"ok": True}


@app.delete("/api/admin/admins/{email:path}")
async def admin_remove_admin_email(
    email: str,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    await _require_admin(session_token)
    try:
        await db.remove_admin_email(email)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        reload=not PRODUCTION,
        workers=1,
    )
