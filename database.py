from pathlib import Path
from typing import Optional
import aiosqlite

DB_PATH = Path(__file__).parent / "data" / "progress.db"


async def init_db() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS mastered_words (
                user_id    INTEGER NOT NULL,
                level      TEXT    NOT NULL,
                word_id    INTEGER NOT NULL,
                mastered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, level, word_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS study_sessions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                level      TEXT    NOT NULL,
                completed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS quiz_sessions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                level      TEXT    NOT NULL,
                score      REAL    NOT NULL,
                completed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)
        await db.commit()


async def get_all_users() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name FROM users ORDER BY name") as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_or_create_user(name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (name) VALUES (?)", (name,)
        )
        await db.commit()
        async with db.execute("SELECT id FROM users WHERE name = ?", (name,)) as cur:
            row = await cur.fetchone()
    return row[0]


async def get_mastered_word_ids(user_id: int, level: str) -> set[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT word_id FROM mastered_words WHERE user_id = ? AND level = ?",
            (user_id, level),
        ) as cur:
            rows = await cur.fetchall()
    return {r[0] for r in rows}


async def add_mastered_word(user_id: int, level: str, word_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO mastered_words (user_id, level, word_id) VALUES (?, ?, ?)",
            (user_id, level, word_id),
        )
        await db.commit()


async def remove_mastered_word(user_id: int, level: str, word_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM mastered_words WHERE user_id = ? AND level = ? AND word_id = ?",
            (user_id, level, word_id),
        )
        await db.commit()


async def reset_mastered_words(user_id: int, level: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM mastered_words WHERE user_id = ? AND level = ?",
            (user_id, level),
        )
        await db.commit()


async def add_study_session(user_id: int, level: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO study_sessions (user_id, level) VALUES (?, ?)",
            (user_id, level),
        )
        await db.commit()


async def get_study_stats(user_id: int, level: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*), MAX(completed_at) FROM study_sessions WHERE user_id = ? AND level = ?",
            (user_id, level),
        ) as cur:
            row = await cur.fetchone()
    return {"count": row[0] or 0, "last_at": row[1]}


async def reset_study_sessions(user_id: int, level: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM study_sessions WHERE user_id = ? AND level = ?",
            (user_id, level),
        )
        await db.commit()


async def add_quiz_session(user_id: int, level: str, score: float) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO quiz_sessions (user_id, level, score) VALUES (?, ?, ?)",
            (user_id, level, score),
        )
        await db.commit()


async def get_quiz_stats(user_id: int, level: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*), MAX(completed_at), MAX(score) FROM quiz_sessions WHERE user_id = ? AND level = ?",
            (user_id, level),
        ) as cur:
            row = await cur.fetchone()
    return {"count": row[0] or 0, "last_at": row[1], "best_score": row[2]}


async def reset_quiz_sessions(user_id: int, level: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM quiz_sessions WHERE user_id = ? AND level = ?",
            (user_id, level),
        )
        await db.commit()
