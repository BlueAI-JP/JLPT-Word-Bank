import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Optional

PRODUCTION = os.getenv("PRODUCTION", "false").lower() == "true"
PORT = int(os.getenv("PORT", "8000"))

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
# Session helpers
# ---------------------------------------------------------------------------

def _require_session(session_token: Optional[str]) -> int:
    if not session_token or session_token not in _sessions:
        raise HTTPException(status_code=401, detail="未登入")
    return _sessions[session_token]


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


# ---------------------------------------------------------------------------
# Routes - Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Routes - Auth
# ---------------------------------------------------------------------------

@app.get("/api/users")
async def list_users():
    return await db.get_all_users()


@app.post("/api/login")
async def login(req: LoginRequest, response: Response):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名字不能為空")
    user_id = await db.get_or_create_user(name)
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
    return {"user_id": user_id, "name": name, "token": token}


@app.post("/api/logout")
async def logout(response: Response, session_token: Annotated[Optional[str], Cookie()] = None):
    if session_token and session_token in _sessions:
        del _sessions[session_token]
    response.delete_cookie("session_token")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Routes - Levels & Words
# ---------------------------------------------------------------------------

@app.get("/api/levels")
async def get_levels():
    return loader.get_all_levels()


@app.get("/api/words/{level}")
async def get_words(
    level: str,
    user_id: Optional[int] = None,
    exclude_mastered: bool = False,
    session_token: Annotated[Optional[str], Cookie()] = None,
):
    _require_session(session_token)
    words = loader.get_words(level)
    if not words:
        raise HTTPException(status_code=404, detail=f"等級 {level} 尚無單字資料")

    if exclude_mastered and user_id is not None:
        mastered = await db.get_mastered_word_ids(user_id, level)
        words = [w for w in words if w["id"] not in mastered]

    return words


# ---------------------------------------------------------------------------
# Routes - Audio (protected)
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
    _require_session(session_token)
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
