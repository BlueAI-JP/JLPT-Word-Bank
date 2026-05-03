import json
from pathlib import Path
from typing import Optional
import aiosqlite

DB_PATH = Path(__file__).parent / "data" / "progress.db"

# ---------------------------------------------------------------------------
# Anonymous daily limits
# ---------------------------------------------------------------------------
ANON_STUDY_LIMIT = 2
ANON_QUIZ_LIMIT  = 1

# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
DEFAULT_ADMIN = "bluejp.lin@gmail.com"


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
            CREATE TABLE IF NOT EXISTS admin_emails (
                email TEXT PRIMARY KEY NOT NULL
            );
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id   INTEGER PRIMARY KEY NOT NULL,
                banned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                banned_by TEXT,
                reason    TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS book_read_progress (
                user_id    INTEGER NOT NULL,
                level      TEXT    NOT NULL,
                queue      TEXT    NOT NULL DEFAULT '[]',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, level),
                FOREIGN KEY (user_id) REFERENCES users(id)
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
            "ALTER TABLE users ADD COLUMN login_count  INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN is_vip       INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                await db.execute(col_sql)
            except Exception:
                pass  # Column already exists

        # Ensure the single shared anonymous user exists
        await db.execute(
            "INSERT OR IGNORE INTO users (name, is_anonymous) VALUES ('Anonymous', 1)"
        )
        # Ensure the default admin exists
        await db.execute(
            "INSERT OR IGNORE INTO admin_emails (email) VALUES (?)", (DEFAULT_ADMIN,)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

async def get_or_create_google_user(
    google_id: str, name: str, email: str, avatar_url: str, ip: str
) -> tuple[int, bool]:
    """Upsert a Google-authenticated user; returns (user_id, is_new)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM users WHERE google_id = ?", (google_id,)
        ) as cur:
            row = await cur.fetchone()

        is_new = False
        if row:
            user_id = row[0]
            await db.execute(
                """UPDATE users
                   SET name = ?, email = ?, avatar_url = ?,
                       last_login = CURRENT_TIMESTAMP, last_ip = ?,
                       login_count = login_count + 1
                   WHERE id = ?""",
                (name, email, avatar_url, ip, user_id),
            )
        else:
            is_new = True
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
                   (google_id, name, email, avatar_url, is_anonymous, last_login, last_ip, login_count)
                   VALUES (?, ?, ?, ?, 0, CURRENT_TIMESTAMP, ?, 1)""",
                (google_id, safe_name, email, avatar_url, ip),
            )
            async with db.execute("SELECT last_insert_rowid()") as cur:
                row = await cur.fetchone()
            user_id = row[0]

        await db.commit()
    return user_id, is_new


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
            "SELECT id, name, email, avatar_url, is_anonymous, is_vip FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "name": row[1], "email": row[2],
        "avatar_url": row[3], "is_anonymous": bool(row[4]), "is_vip": bool(row[5]),
    }


async def delete_user(user_id: int) -> None:
    """Delete a user and all their associated data. Raises ValueError for protected accounts."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Safety: cannot delete anonymous user or default admin
        async with db.execute(
            "SELECT is_anonymous, email FROM users WHERE id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise ValueError("使用者不存在")
        if row[0]:
            raise ValueError("不可刪除匿名使用者帳號")
        if row[1] and row[1].lower() == DEFAULT_ADMIN.lower():
            raise ValueError("不可刪除預設管理者帳號")

        # Delete all associated data first (foreign key order)
        for sql in [
            "DELETE FROM mastered_words      WHERE user_id = ?",
            "DELETE FROM study_sessions      WHERE user_id = ?",
            "DELETE FROM quiz_sessions       WHERE user_id = ?",
            "DELETE FROM quiz_wrong_words    WHERE user_id = ?",
            "DELETE FROM book_read_progress  WHERE user_id = ?",
            "DELETE FROM banned_users        WHERE user_id = ?",
            "DELETE FROM users               WHERE id = ?",
        ]:
            await db.execute(sql, (user_id,))
        await db.commit()


async def set_user_vip(user_id: int, is_vip: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_vip = ? WHERE id = ?",
            (1 if is_vip else 0, user_id),
        )
        await db.commit()


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


# ---------------------------------------------------------------------------
# Book reading progress
# ---------------------------------------------------------------------------

async def get_book_progress(user_id: int, level: str) -> Optional[dict]:
    """Return {queue: [word_id, ...]} or None if never initialized."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT queue FROM book_read_progress WHERE user_id = ? AND level = ?",
            (user_id, level),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return {"queue": json.loads(row[0])}


async def save_book_progress(user_id: int, level: str, queue: list[int]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO book_read_progress (user_id, level, queue, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
            (user_id, level, json.dumps(queue)),
        )
        await db.commit()


async def reset_book_progress(user_id: int, level: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM book_read_progress WHERE user_id = ? AND level = ?",
            (user_id, level),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Admin helpers
# ---------------------------------------------------------------------------

async def is_admin(email: Optional[str]) -> bool:
    if not email:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM admin_emails WHERE email = ?", (email,)
        ) as cur:
            return await cur.fetchone() is not None


async def get_admin_emails() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT email FROM admin_emails ORDER BY email") as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def add_admin_email(email: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO admin_emails (email) VALUES (?)", (email,)
        )
        await db.commit()


async def remove_admin_email(email: str) -> None:
    if email == DEFAULT_ADMIN:
        raise ValueError("不可刪除預設管理者帳號")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admin_emails WHERE email = ?", (email,))
        await db.commit()


async def is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None


async def ban_user(user_id: int, banned_by: str, reason: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO banned_users (user_id, banned_by, reason)
               VALUES (?, ?, ?)""",
            (user_id, banned_by, reason),
        )
        await db.commit()


async def unban_user(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_all_users_admin() -> list[dict]:
    """Return all non-anonymous users with aggregated stats for the admin panel."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT
                u.id,
                u.name,
                u.email,
                u.login_count,
                u.last_login,
                u.last_ip,
                (SELECT COUNT(*) FROM study_sessions s WHERE s.user_id = u.id),
                (SELECT COUNT(*) FROM quiz_sessions  q WHERE q.user_id = u.id),
                (SELECT COUNT(*) FROM mastered_words m WHERE m.user_id = u.id),
                (SELECT MAX(score) FROM quiz_sessions q WHERE q.user_id = u.id),
                CASE WHEN b.user_id IS NOT NULL THEN 1 ELSE 0 END,
                u.is_vip
            FROM users u
            LEFT JOIN banned_users b ON b.user_id = u.id
            WHERE u.is_anonymous = 0
            ORDER BY
                CASE WHEN u.last_login IS NULL THEN 1 ELSE 0 END,
                u.last_login DESC
        """) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": r[0], "name": r[1], "email": r[2] or "",
            "login_count": r[3] or 0, "last_login": r[4], "last_ip": r[5] or "",
            "study_count": r[6], "quiz_count": r[7],
            "mastered_count": r[8], "best_quiz_score": r[9],
            "is_banned": bool(r[10]), "is_vip": bool(r[11]),
        }
        for r in rows
    ]
