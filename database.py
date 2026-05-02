from pathlib import Path
from typing import Optional
import aiosqlite

DB_PATH = Path(__file__).parent / "data" / "progress.db"

# ---------------------------------------------------------------------------
# Anonymous daily limits
# ---------------------------------------------------------------------------
ANON_STUDY_LIMIT = 2
ANON_QUIZ_LIMIT  = 1


async def init_db() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT UNIQUE NOT NULL,
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
            CREATE TABLE IF NOT EXISTS quiz_wrong_words (
                user_id  INTEGER NOT NULL,
                level    TEXT    NOT NULL,
                word_id  INTEGER NOT NULL,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, level, word_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS anonymous_usage (
                ip          TEXT NOT NULL,
                date        TEXT NOT NULL,
                study_count INTEGER NOT NULL DEFAULT 0,
                quiz_count  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (ip, date)
            );
        """)

        # Migrate: add new columns to users (each isolated, ignore if exists)
        for col_sql in [
            "ALTER TABLE users ADD COLUMN google_id   TEXT UNIQUE",
            "ALTER TABLE users ADD COLUMN email        TEXT",
            "ALTER TABLE users ADD COLUMN avatar_url   TEXT",
            "ALTER TABLE users ADD COLUMN is_anonymous INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN last_login   DATETIME",
            "ALTER TABLE users ADD COLUMN last_ip      TEXT",
        ]:
            try:
                await db.execute(col_sql)
            except Exception:
                pass  # Column already exists

        # Ensure the single shared anonymous user exists
        await db.execute(
            "INSERT OR IGNORE INTO users (name, is_anonymous) VALUES ('Anonymous', 1)"
        )
        await db.commit()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

async def get_all_users() -> list[str]:
    """Return non-anonymous user names (admin / backward compat)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT name FROM users WHERE is_anonymous = 0 ORDER BY name"
        ) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_or_create_user(name: str) -> int:
    """Legacy name-based login."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (name) VALUES (?)", (name,)
        )
        await db.commit()
        async with db.execute("SELECT id FROM users WHERE name = ?", (name,)) as cur:
            row = await cur.fetchone()
    return row[0]


async def get_or_create_google_user(
    google_id: str, name: str, email: str, avatar_url: str, ip: str
) -> int:
    """Upsert a Google-authenticated user; returns user_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM users WHERE google_id = ?", (google_id,)
        ) as cur:
            row = await cur.fetchone()

        if row:
            user_id = row[0]
            await db.execute(
                """UPDATE users
                   SET name = ?, email = ?, avatar_url = ?,
                       last_login = CURRENT_TIMESTAMP, last_ip = ?
                   WHERE id = ?""",
                (name, email, avatar_url, ip, user_id),
            )
        else:
            # Ensure name uniqueness
            safe_name = name
            suffix = 0
            while True:
                async with db.execute(
                    "SELECT 1 FROM users WHERE name = ?", (safe_name,)
                ) as cur:
                    exists = await cur.fetchone()
                if not exists:
                    break
                suffix += 1
                safe_name = f"{name}_{suffix}"

            await db.execute(
                """INSERT INTO users
                   (google_id, name, email, avatar_url, is_anonymous, last_login, last_ip)
                   VALUES (?, ?, ?, ?, 0, CURRENT_TIMESTAMP, ?)""",
                (google_id, safe_name, email, avatar_url, ip),
            )
            async with db.execute("SELECT last_insert_rowid()") as cur:
                row = await cur.fetchone()
            user_id = row[0]

        await db.commit()
    return user_id


async def get_anonymous_user_id() -> int:
    """Return the shared anonymous user's id (always exists after init_db)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM users WHERE is_anonymous = 1 LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
    return row[0]


async def get_user_by_id(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, name, email, avatar_url, is_anonymous FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "name": row[1], "email": row[2],
        "avatar_url": row[3], "is_anonymous": bool(row[4]),
    }


# ---------------------------------------------------------------------------
# Anonymous usage tracking
# ---------------------------------------------------------------------------

async def get_anonymous_usage(ip: str, date: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT study_count, quiz_count FROM anonymous_usage WHERE ip = ? AND date = ?",
            (ip, date),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {"study_count": 0, "quiz_count": 0}
    return {"study_count": row[0], "quiz_count": row[1]}


async def increment_anonymous_usage(ip: str, date: str, mode: str) -> None:
    col = "study_count" if mode == "study" else "quiz_count"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO anonymous_usage (ip, date) VALUES (?, ?)",
            (ip, date),
        )
        await db.execute(
            f"UPDATE anonymous_usage SET {col} = {col} + 1 WHERE ip = ? AND date = ?",
            (ip, date),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Mastered words
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Study sessions
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Quiz sessions
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Quiz wrong words
# ---------------------------------------------------------------------------

async def add_quiz_wrong_words_batch(user_id: int, level: str, word_ids: list[int]) -> None:
    if not word_ids:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT OR IGNORE INTO quiz_wrong_words (user_id, level, word_id) VALUES (?, ?, ?)",
            [(user_id, level, wid) for wid in word_ids],
        )
        await db.commit()


async def get_quiz_wrong_word_ids(user_id: int, level: str) -> set[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT word_id FROM quiz_wrong_words WHERE user_id = ? AND level = ?",
            (user_id, level),
        ) as cur:
            rows = await cur.fetchall()
    return {r[0] for r in rows}


async def remove_quiz_wrong_word(user_id: int, level: str, word_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM quiz_wrong_words WHERE user_id = ? AND level = ? AND word_id = ?",
            (user_id, level, word_id),
        )
        await db.commit()


async def reset_quiz_wrong_words(user_id: int, level: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM quiz_wrong_words WHERE user_id = ? AND level = ?",
            (user_id, level),
        )
        await db.commit()
