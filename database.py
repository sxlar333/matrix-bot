import sqlite3
import time
import os
from contextlib import contextmanager


DB = "greg.db"


def get_db():

    path = os.path.abspath(DB)

    print(f"📂 Database: {path}")

    return sqlite3.connect(DB)


@contextmanager
def db_session():
    """Open a short-lived connection that commits and closes automatically."""
    db = get_db()
    try:
        cur = db.cursor()
        yield cur
        db.commit()
    finally:
        db.close()


def setup():

    print("🧠 Setting up Greg's brain...")

    with db_session() as cur:

        cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT,
            user TEXT,
            message TEXT,
            timestamp INTEGER
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory TEXT,
            importance INTEGER,
            timestamp INTEGER
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS greg_events (
            event_id TEXT PRIMARY KEY,
            timestamp INTEGER
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            timestamp INTEGER
        )
        """)

        # Database upgrades
        columns = []

        cur.execute(
            "PRAGMA table_info(memories)"
        )

        for column in cur.fetchall():
            columns.append(column[1])

        if "timestamp" not in columns:

            print(
                "🧠 Upgrading memories table..."
            )

            cur.execute(
                """
                ALTER TABLE memories
                ADD COLUMN timestamp INTEGER
                """
            )

            print(
                "✅ Added timestamp column"
            )

    print("✅ Greg brain ready")


def save_message(room, user, message):

    print("💾 Saving message")
    print(
        f"   {user}: {message}"
    )

    with db_session() as cur:

        cur.execute(
            """
            INSERT INTO messages
            (
                room,
                user,
                message,
                timestamp
            )
            VALUES (?,?,?,?)
            """,
            (
                room,
                user,
                message,
                int(time.time())
            )
        )

        message_id = cur.lastrowid

    print(
        "✅ Message saved"
    )

    return message_id


def get_recent_messages(room, limit=50, exclude_ids=None):
    """Load recent history, optionally skipping specific message ids.

    The current turn's messages are excluded so the model only sees them
    once (as the newest prompt) instead of also inside the history.
    """
    print(
        "📜 Loading chat history..."
    )

    exclude_ids = exclude_ids or []

    if exclude_ids:

        placeholders = ",".join(
            "?" * len(exclude_ids)
        )

        sql = f"""
            SELECT user, message
            FROM messages
            WHERE room=? AND id NOT IN ({placeholders})
            ORDER BY id DESC
            LIMIT ?
        """

        params = (room, *exclude_ids, limit)

    else:

        sql = """
            SELECT user, message
            FROM messages
            WHERE room=?
            ORDER BY id DESC
            LIMIT ?
        """

        params = (room, limit)

    with db_session() as cur:

        cur.execute(
            sql,
            params
        )

        rows = cur.fetchall()

    rows.reverse()

    messages = []

    for user, message in rows:

        # Greg's own replies are assistant turns,
        # everyone else is a user turn
        if user == "greg":

            messages.append(
                {
                    "role": "assistant",
                    "content": message
                }
            )

        else:

            messages.append(
                {
                    "role": "user",
                    "content":
                    f"{user}: {message}"
                }
            )

    print(
        f"✅ Loaded {len(messages)} messages"
    )

    return messages


def save_memory(memory, importance):

    print("🧠 Saving memory:")
    print(
        memory
    )

    with db_session() as cur:

        cur.execute(
            """
            INSERT INTO memories
            (
                memory,
                importance,
                timestamp
            )
            VALUES (?,?,?)
            """,
            (
                memory,
                importance,
                int(time.time())
            )
        )

    print(
        "✅ Memory saved"
    )


def get_memories(limit=10):

    with db_session() as cur:

        cur.execute(
            """
            SELECT memory
            FROM memories
            ORDER BY importance DESC, id DESC
            LIMIT ?
            """,
            (
                limit,
            )
        )

        rows = cur.fetchall()

    memories = [
        row[0]
        for row in rows
    ]

    print(
        f"🧠 Loaded {len(memories)} memories"
    )

    return memories


def list_memories():
    """Return every memory as (id, memory, importance), best first."""
    with db_session() as cur:

        cur.execute(
            """
            SELECT id, memory, importance
            FROM memories
            ORDER BY importance DESC, id DESC
            """
        )

        rows = cur.fetchall()

    print(
        f"🧠 Listed {len(rows)} memories"
    )

    return rows


def delete_memory(identifier):
    """Delete memories by id, or by fuzzy text match.

    Returns the number of rows removed.
    """
    if isinstance(identifier, int):

        with db_session() as cur:

            cur.execute(
                """
                DELETE FROM memories
                WHERE id=?
                """,
                (identifier,)
            )

            count = cur.rowcount

    else:

        with db_session() as cur:

            cur.execute(
                """
                DELETE FROM memories
                WHERE memory LIKE ?
                """,
                (
                    f"%{identifier}%",
                )
            )

            count = cur.rowcount

    print(
        f"🧠 Removed {count} memory/memories"
    )

    return count


def save_greg_event(event_id):

    print("📌 Saving Greg event:", event_id)

    with db_session() as cur:

        cur.execute(
            """
            INSERT OR REPLACE INTO greg_events
            (event_id, timestamp)
            VALUES (?,?)
            """,
            (
                event_id,
                int(time.time())
            )
        )

    print("✅ Greg event saved")


def is_greg_event(event_id):

    with db_session() as cur:

        cur.execute(
            """
            SELECT 1
            FROM greg_events
            WHERE event_id=?
            """,
            (event_id,)
        )

        found = cur.fetchone() is not None

    print(
        f"🔎 Greg event found: {found}"
    )

    return found


def get_setting(key, default=None):
    """Read a stored setting. Returns default if unset."""
    with db_session() as cur:

        cur.execute(
            """
            SELECT value
            FROM settings
            WHERE key=?
            """,
            (key,)
        )

        row = cur.fetchone()

    if row is None:
        return default

    return row[0]


def set_setting(key, value):
    """Store a setting. A value of None removes it entirely."""
    with db_session() as cur:

        if value is None:

            cur.execute(
                """
                DELETE FROM settings
                WHERE key=?
                """,
                (key,)
            )

        else:

            cur.execute(
                """
                INSERT OR REPLACE INTO settings
                (key, value, timestamp)
                VALUES (?,?,?)
                """,
                (
                    key,
                    value,
                    int(time.time())
                )
            )

    print(
        f"⚙️ Setting {key} = {value}"
    )


def list_settings():
    """Return every setting as (key, value, timestamp)."""
    with db_session() as cur:

        cur.execute(
            """
            SELECT key, value, timestamp
            FROM settings
            ORDER BY key
            """
        )

        rows = cur.fetchall()

    return rows
