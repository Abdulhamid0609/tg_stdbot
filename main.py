import datetime as dt
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

DB_PATH = os.environ.get("CARPE_DIEM_DB", "carpe_diem.sqlite3")
DEFAULT_THREAD_ID = 0
REPORT_CHECK_MINUTES = 10

LOGGER = logging.getLogger(__name__)


@dataclass
class DailyResult:
    date: str
    goal: int
    penalty: int


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                deadline_time TEXT,
                username TEXT,
                first_name TEXT,
                last_name TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                deadline_time TEXT,
                PRIMARY KEY (chat_id, thread_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                task_index INTEGER NOT NULL,
                description TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                proof TEXT,
                proof_file_id TEXT,
                proof_message_id INTEGER,
                UNIQUE(user_id, chat_id, thread_id, date, task_index)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                goal INTEGER NOT NULL DEFAULT 0,
                penalty INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, chat_id, thread_id, date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                PRIMARY KEY (chat_id, thread_id, date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                goal_delta INTEGER NOT NULL DEFAULT 0,
                penalty_delta INTEGER NOT NULL DEFAULT 0,
                note TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        ensure_column(conn, "users", "username", "TEXT")
        ensure_column(conn, "users", "first_name", "TEXT")
        ensure_column(conn, "users", "last_name", "TEXT")
        ensure_column(conn, "tasks", "chat_id", "INTEGER", "user_id")
        ensure_column(conn, "tasks", "thread_id", "INTEGER", str(DEFAULT_THREAD_ID))
        ensure_column(conn, "tasks", "proof_file_id", "TEXT")
        ensure_column(conn, "tasks", "proof_message_id", "INTEGER")
        ensure_column(conn, "results", "chat_id", "INTEGER", "user_id")
        ensure_column(conn, "results", "thread_id", "INTEGER", str(DEFAULT_THREAD_ID))
        ensure_column(conn, "chats", "deadline_time", "TEXT")
        ensure_column(conn, "reports", "thread_id", "INTEGER", str(DEFAULT_THREAD_ID))


def ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
    fill_expression: str | None = None,
) -> None:
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    if column in columns:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    if fill_expression is not None:
        conn.execute(
            f"UPDATE {table} SET {column} = {fill_expression} WHERE {column} IS NULL"
        )


def update_user_profile(conn: sqlite3.Connection, user) -> None:
    conn.execute(
        """
        INSERT INTO users (user_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name
        """,
        (user.id, user.username, user.first_name, user.last_name),
    )


def get_deadline_time(conn: sqlite3.Connection, user_id: int) -> str | None:
    row = conn.execute(
        "SELECT deadline_time FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return row[0] if row else None


def set_deadline_time(conn: sqlite3.Connection, user_id: int, deadline: str) -> None:
    conn.execute(
        "INSERT INTO users (user_id, deadline_time) VALUES (?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET deadline_time = excluded.deadline_time",
        (user_id, deadline),
    )


def get_chat_deadline(conn: sqlite3.Connection, chat_id: int, thread_id: int) -> str | None:
    row = conn.execute(
        "SELECT deadline_time FROM chats WHERE chat_id = ? AND thread_id = ?",
        (chat_id, thread_id),
    ).fetchone()
    if row and row[0]:
        return row[0]
    fallback = conn.execute(
        "SELECT deadline_time FROM chats WHERE chat_id = ? AND thread_id = ?",
        (chat_id, DEFAULT_THREAD_ID),
    ).fetchone()
    return fallback[0] if fallback else None


def set_chat_deadline(
    conn: sqlite3.Connection, chat_id: int, thread_id: int, deadline: str
) -> None:
    conn.execute(
        """
        INSERT INTO chats (chat_id, thread_id, deadline_time)
        VALUES (?, ?, ?)
        ON CONFLICT(chat_id, thread_id) DO UPDATE SET deadline_time = excluded.deadline_time
        """,
        (chat_id, thread_id, deadline),
    )


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def parse_time(value: str) -> dt.time:
    return dt.time.fromisoformat(value)


def format_date(value: dt.date) -> str:
    return value.isoformat()


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def deadline_datetime(target_date: dt.date, deadline_time: dt.time) -> dt.datetime:
    return dt.datetime.combine(target_date, deadline_time, tzinfo=dt.timezone.utc)


def is_after_deadline(target_date: dt.date, deadline_time: dt.time) -> bool:
    return utc_now() > deadline_datetime(target_date, deadline_time)


def fetch_tasks(
    conn: sqlite3.Connection, user_id: int, chat_id: int, thread_id: int, date: str
) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT * FROM tasks
        WHERE user_id = ? AND chat_id = ? AND thread_id = ? AND date = ?
        ORDER BY task_index
        """,
        (user_id, chat_id, thread_id, date),
    ).fetchall()


def fetch_tasks_across_threads(
    conn: sqlite3.Connection, user_id: int, chat_id: int, date: str
) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT * FROM tasks
        WHERE user_id = ? AND chat_id = ? AND date = ?
        ORDER BY thread_id, task_index
        """,
        (user_id, chat_id, date),
    ).fetchall()


def fetch_results(
    conn: sqlite3.Connection, user_id: int, chat_id: int, thread_id: int
) -> list[DailyResult]:
    rows = conn.execute(
        """
        SELECT date, goal, penalty FROM results
        WHERE user_id = ? AND chat_id = ? AND thread_id = ?
        ORDER BY date
        """,
        (user_id, chat_id, thread_id),
    ).fetchall()
    return [DailyResult(date=row[0], goal=row[1], penalty=row[2]) for row in rows]


def fetch_adjustment_totals(conn: sqlite3.Connection, user_id: int, chat_id: int) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(goal_delta), 0), COALESCE(SUM(penalty_delta), 0)
        FROM adjustments
        WHERE user_id = ? AND chat_id = ?
        """,
        (user_id, chat_id),
    ).fetchone()
    if not row:
        return 0, 0
    return row[0], row[1]


def resolve_target_user_id(conn: sqlite3.Connection, target: str) -> int | None:
    target_value = target.strip()
    if target_value.startswith("@"):
        username = target_value.lstrip("@")
        row = conn.execute(
            "SELECT user_id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return row[0] if row else None
    if target_value.isdigit():
        return int(target_value)
    return None


def evaluate_daily_result(
    conn: sqlite3.Connection,
    user_id: int,
    chat_id: int,
    thread_id: int,
    date: dt.date,
    deadline_time: dt.time,
) -> DailyResult | None:
    if not is_after_deadline(date, deadline_time):
        return None

    existing = conn.execute(
        """
        SELECT date, goal, penalty FROM results
        WHERE user_id = ? AND chat_id = ? AND thread_id = ? AND date = ?
        """,
        (user_id, chat_id, thread_id, date.isoformat()),
    ).fetchone()
    if existing:
        return DailyResult(date=existing[0], goal=existing[1], penalty=existing[2])

    tasks = fetch_tasks(conn, user_id, chat_id, thread_id, date.isoformat())
    if not tasks:
        return None

    all_done = all(task["completed"] == 1 for task in tasks)
    goal = 1 if all_done else 0
    penalty = 0 if all_done else 1

    conn.execute(
        """
        INSERT INTO results (user_id, chat_id, thread_id, date, goal, penalty)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, chat_id, thread_id, date.isoformat(), goal, penalty),
    )
    return DailyResult(date=date.isoformat(), goal=goal, penalty=penalty)


def format_tasks(tasks: Iterable[sqlite3.Row]) -> str:
    lines = []
    for task in tasks:
        status = "✅" if task["completed"] == 1 else "❌"
        proof = f" (proof: {task['proof']})" if task["proof"] else ""
        photo = " (photo attached)" if task["proof_file_id"] else ""
        lines.append(
            f"{status} {task['task_index']}. {task['description']}{proof}{photo}"
        )
    return "\n".join(lines)


def format_tasks_with_links(
    tasks: Iterable[sqlite3.Row], chat_or_id, show_thread: bool = False
) -> str:
    lines = []
    for task in tasks:
        status = "✅" if task["completed"] == 1 else "❌"
        link = None
        if task["proof_message_id"]:
            link = message_link(chat_or_id, task["proof_message_id"])
        link_text = f" (proof: {link})" if link else ""
        thread_text = ""
        if show_thread and task["thread_id"] != DEFAULT_THREAD_ID:
            thread_text = f" [topic {task['thread_id']}]"
        lines.append(
            f"{status} {task['task_index']}. {task['description']}{thread_text}{link_text}"
        )
    return "\n".join(lines)


def help_text() -> str:
    return (
        "Commands:\n"
        "/start - Welcome message\n"
        "/help - Show this help\n"
        "/setdeadline HH:MM (UTC) - Set the group deadline once (admin only, all topics)\n"
        "/tasks [YYYY-MM-DD] + tasks on new lines - Add tasks for the day\n"
        "/done [YYYY-MM-DD] TASK_NUMBER proof - Mark a task done (photos supported)\n"
        "/status [YYYY-MM-DD|@user|all] - Show task status and result (all topics)\n"
        "/score - Show total goals and penalties\n"
        "/leaderboard - Show top goals and penalties (all topics)\n"
        "/addscore @user GOALS PENALTIES [note] - Admin score restore"
    )


def get_thread_id(update: Update) -> int:
    message = update.effective_message
    return message.message_thread_id if message and message.message_thread_id else DEFAULT_THREAD_ID


def get_command_text(update: Update) -> str:
    message = update.effective_message
    if not message:
        return ""
    return message.text or message.caption or ""


def parse_args_from_text(text: str) -> list[str]:
    return text.split()[1:] if text else []


def parse_optional_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return parse_date(value)
    except ValueError:
        return None


def user_is_admin(member) -> bool:
    return member.status in {"administrator", "creator"}


def get_proof_message(update: Update):
    message = update.effective_message
    if not message:
        return None
    if message.photo:
        return message
    if message.reply_to_message and message.reply_to_message.photo:
        return message.reply_to_message
    return None


def message_link(chat_or_id, message_id: int) -> str | None:
    if not chat_or_id or not message_id:
        return None
    if hasattr(chat_or_id, "username") and chat_or_id.username:
        return f"https://t.me/{chat_or_id.username}/{message_id}"
    chat_id = chat_or_id.id if hasattr(chat_or_id, "id") else chat_or_id
    if isinstance(chat_id, int) and chat_id < 0:
        internal_id = str(chat_id)[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"
    return None


def format_user_label(row: sqlite3.Row) -> str:
    name_parts = [row["first_name"], row["last_name"]]
    full_name = " ".join(part for part in name_parts if part)
    if row["username"]:
        return f"@{row['username']}"
    if full_name:
        return full_name
    return f"User {row['user_id']}"


def build_daily_report(
    conn: sqlite3.Connection, chat_id: int, thread_id: int, date_text: str
) -> tuple[str, list[int]] | None:
    conn.row_factory = sqlite3.Row
    user_rows = conn.execute(
        """
        SELECT DISTINCT tasks.user_id, users.username, users.first_name, users.last_name
        FROM tasks
        LEFT JOIN users ON users.user_id = tasks.user_id
        WHERE tasks.chat_id = ? AND tasks.thread_id = ? AND tasks.date = ?
        ORDER BY COALESCE(users.username, users.first_name, users.last_name, tasks.user_id)
        """,
        (chat_id, thread_id, date_text),
    ).fetchall()
    if not user_rows:
        return None

    entries = []
    deadline_text = get_chat_deadline(conn, chat_id, thread_id)
    if not deadline_text:
        return None
    deadline_time = parse_time(deadline_text)
    task_date = parse_date(date_text)
    if not is_after_deadline(task_date, deadline_time):
        return None

    for user in user_rows:
        tasks = fetch_tasks(conn, user["user_id"], chat_id, thread_id, date_text)
        if not tasks:
            continue
        result = evaluate_daily_result(
            conn,
            user["user_id"],
            chat_id,
            thread_id,
            task_date,
            deadline_time,
        )
        if not result:
            continue
        outcome = "+1 GOAL ✅" if result.goal == 1 else "+1 PENALTY ❌"
        task_lines = format_tasks_with_links(tasks, chat_id).splitlines()
        entries.append("\n".join([f"- {format_user_label(user)}: {outcome}", *task_lines]))

    if not entries:
        return None

    report_text = "\n".join([f"Daily results for {date_text}:", *entries])
    return report_text, [row["user_id"] for row in user_rows]


async def send_daily_reports(context: ContextTypes.DEFAULT_TYPE) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        task_dates = conn.execute(
            """
            SELECT DISTINCT chat_id, thread_id, date FROM tasks
            ORDER BY date
            """
        ).fetchall()
        for row in task_dates:
            chat_id = row["chat_id"]
            thread_id = row["thread_id"]
            date_text = row["date"]
            reserved = conn.execute(
                "INSERT OR IGNORE INTO reports (chat_id, thread_id, date) VALUES (?, ?, ?)",
                (chat_id, thread_id, date_text),
            )
            if reserved.rowcount == 0:
                continue
            conn.commit()

            report = build_daily_report(conn, chat_id, thread_id, date_text)
            if not report:
                conn.execute(
                    "DELETE FROM reports WHERE chat_id = ? AND thread_id = ? AND date = ?",
                    (chat_id, thread_id, date_text),
                )
                conn.commit()
                continue

            report_text, _ = report
            kwargs = {"chat_id": chat_id, "text": report_text}
            if thread_id != DEFAULT_THREAD_ID:
                kwargs["message_thread_id"] = thread_id
            try:
                await context.bot.send_message(**kwargs)
            except Exception:
                conn.execute(
                    "DELETE FROM reports WHERE chat_id = ? AND thread_id = ? AND date = ?",
                    (chat_id, thread_id, date_text),
                )
                conn.commit()
                LOGGER.exception(
                    "Failed to send daily report for chat=%s thread=%s date=%s",
                    chat_id,
                    thread_id,
                    date_text,
                )
                continue

            conn.commit()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = (
        "Welcome to Carpe Diem!\n"
        "Use /setdeadline HH:MM (UTC) to set the group deadline once (admin only).\n"
        "Send tasks with /tasks [YYYY-MM-DD] followed by each task on a new line.\n"
        "Mark tasks done with /done [YYYY-MM-DD] TASK_NUMBER proof text.\n"
        "Use /help for the full command list."
    )
    await update.message.reply_text(message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(help_text())


async def set_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Usage: /setdeadline HH:MM\nExample: /setdeadline 20:00"
        )
        return

    try:
        deadline_time = parse_time(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid time. Use HH:MM (24h).")
        return

    thread_id = DEFAULT_THREAD_ID
    member = await context.bot.get_chat_member(
        update.effective_chat.id, update.effective_user.id
    )
    if not user_is_admin(member):
        await update.message.reply_text("Only admins can set the group deadline.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        update_user_profile(conn, update.effective_user)
        set_chat_deadline(
            conn, update.effective_chat.id, thread_id, deadline_time.isoformat()
        )

    await update.message.reply_text(
        f"Deadline set to {deadline_time.strftime('%H:%M')} UTC. This applies to all topics."
    )


async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    command_text = get_command_text(update)
    args = context.args if context.args else parse_args_from_text(command_text)
    if args:
        parsed_date = parse_optional_date(args[0])
        if parsed_date:
            date_text = format_date(parsed_date)
        else:
            date_text = format_date(utc_now().date())
    else:
        date_text = format_date(utc_now().date())

    raw_text = command_text
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    task_lines = lines[1:] if lines and lines[0].startswith("/tasks") else lines

    if not task_lines:
        await update.message.reply_text(
            "Please provide at least one task on a new line.\n"
            "Example:\n"
            "/tasks 2026-01-23\nReading a book\nRunning 2 kms"
        )
        return

    thread_id = get_thread_id(update)

    with sqlite3.connect(DB_PATH) as conn:
        update_user_profile(conn, update.effective_user)
        deadline_text = get_chat_deadline(conn, update.effective_chat.id, thread_id)
        if not deadline_text:
            await update.message.reply_text(
                "Set a deadline first with /setdeadline HH:MM (admin only)."
            )
            return
        row = conn.execute(
            """
            SELECT COALESCE(MAX(task_index), 0)
            FROM tasks
            WHERE user_id = ? AND chat_id = ? AND thread_id = ? AND date = ?
            """,
            (
                update.effective_user.id,
                update.effective_chat.id,
                thread_id,
                date_text,
            ),
        ).fetchone()
        start_index = (row[0] if row else 0) + 1

        for offset, task in enumerate(task_lines, start=0):
            conn.execute(
                "INSERT INTO tasks (user_id, chat_id, thread_id, date, task_index, description)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    update.effective_user.id,
                    update.effective_chat.id,
                    thread_id,
                    date_text,
                    start_index + offset,
                    task,
                ),
            )

    await update.message.reply_text(
        f"Added {len(task_lines)} task(s) for {date_text}."
    )


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    command_text = get_command_text(update)
    args = context.args if context.args else parse_args_from_text(command_text)
    if len(args) < 1:
        await update.message.reply_text(
            "Usage: /done [YYYY-MM-DD] TASK_NUMBER proof text\nExample: /done 1 Finished chapter 5"
        )
        return

    parsed_date = parse_optional_date(args[0])
    if parsed_date:
        date_text = format_date(parsed_date)
        task_arg_index = 1
    else:
        date_text = format_date(utc_now().date())
        task_arg_index = 0

    if len(args) <= task_arg_index:
        await update.message.reply_text(
            "Usage: /done [YYYY-MM-DD] TASK_NUMBER proof text\nExample: /done 1 Finished chapter 5"
        )
        return

    task_number_text = args[task_arg_index]
    proof = " ".join(args[task_arg_index + 1 :]).strip() if len(args) > task_arg_index + 1 else ""

    task_date = parse_date(date_text)

    if not task_number_text.isdigit():
        await update.message.reply_text(
            "Task number must be numeric. Example: /done 2 Completed the run"
        )
        return

    task_number = int(task_number_text)
    proof_message = get_proof_message(update)
    photo_file_id = (
        proof_message.photo[-1].file_id if proof_message and proof_message.photo else None
    )
    proof_message_id = proof_message.message_id if proof_message else update.effective_message.message_id

    thread_id = get_thread_id(update)
    all_done_now = False
    total_tasks = 0

    with sqlite3.connect(DB_PATH) as conn:
        update_user_profile(conn, update.effective_user)
        deadline_text = get_chat_deadline(conn, update.effective_chat.id, thread_id)
        if not deadline_text:
            await update.message.reply_text(
                "Set a deadline first with /setdeadline HH:MM (admin only)."
            )
            return

        deadline_time = parse_time(deadline_text)
        if is_after_deadline(task_date, deadline_time):
            await update.message.reply_text(
                "Deadline passed for that date. Proofs can no longer be accepted."
            )
            return

        row = conn.execute(
            """
            SELECT id FROM tasks
            WHERE user_id = ? AND chat_id = ? AND thread_id = ? AND date = ? AND task_index = ?
            """,
            (
                update.effective_user.id,
                update.effective_chat.id,
                thread_id,
                date_text,
                task_number,
            ),
        ).fetchone()
        if not row:
            await update.message.reply_text("Task not found for that date.")
            return

        conn.execute(
            "UPDATE tasks SET completed = 1, proof = ?, proof_file_id = ?, proof_message_id = ? WHERE id = ?",
            (proof if proof else None, photo_file_id, proof_message_id, row[0]),
        )

        tasks_list = fetch_tasks(
            conn,
            update.effective_user.id,
            update.effective_chat.id,
            thread_id,
            date_text,
        )
        total_tasks = len(tasks_list)
        all_done_now = bool(tasks_list) and all(task["completed"] == 1 for task in tasks_list)

    await update.message.reply_text(
        f"Marked task {task_number} done for {date_text}."
    )
    if all_done_now:
        await update.message.reply_text(
            f"🎉 All done for {date_text}! You completed all {total_tasks} task(s)."
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    command_text = get_command_text(update)
    args = context.args if context.args else parse_args_from_text(command_text)
    date_text = format_date(utc_now().date())
    target_arg = ""
    if args:
        first = args[0]
        parsed_date = parse_optional_date(first)
        if parsed_date:
            date_text = format_date(parsed_date)
            target_arg = args[1] if len(args) > 1 else ""
        else:
            target_arg = first
            if len(args) > 1:
                parsed_date = parse_optional_date(args[1])
                if parsed_date:
                    date_text = format_date(parsed_date)

    task_date = parse_date(date_text)

    thread_id = get_thread_id(update)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        update_user_profile(conn, update.effective_user)
        if target_arg in {"all"} or target_arg.startswith("@"):
            member = await context.bot.get_chat_member(
                update.effective_chat.id, update.effective_user.id
            )
            if not user_is_admin(member):
                await update.message.reply_text("Only admins can view other users' status.")
                return

        deadline_text = get_chat_deadline(conn, update.effective_chat.id, DEFAULT_THREAD_ID)
        if not deadline_text:
            await update.message.reply_text(
                "Set a deadline first with /setdeadline HH:MM (admin only)."
            )
            return

        deadline_time = parse_time(deadline_text)
        if target_arg == "all":
            user_rows = conn.execute(
                """
                SELECT DISTINCT tasks.user_id, users.username, users.first_name, users.last_name
                FROM tasks
                LEFT JOIN users ON users.user_id = tasks.user_id
                WHERE tasks.chat_id = ? AND tasks.date = ?
                ORDER BY COALESCE(users.username, users.first_name, users.last_name, tasks.user_id)
                """,
                (update.effective_chat.id, date_text),
            ).fetchall()
            if not user_rows:
                await update.message.reply_text(
                    "No tasks saved for that date. Use /tasks to add tasks first."
                )
                return
            sections = []
            for user in user_rows:
                tasks_list = fetch_tasks_across_threads(
                    conn, user["user_id"], update.effective_chat.id, date_text
                )
                if not tasks_list:
                    continue
                if is_after_deadline(task_date, deadline_time):
                    all_done = all(task["completed"] == 1 for task in tasks_list)
                    outcome = "GOAL ✅" if all_done else "PENALTY ❌"
                else:
                    outcome = "pending ⏳"
                sections.append(
                    "\n".join(
                        [
                            f"{format_user_label(user)} ({outcome}):",
                            format_tasks_with_links(
                                tasks_list, update.effective_chat, show_thread=True
                            ),
                        ]
                    )
                )
            await update.message.reply_text("\n\n".join(sections))
            return

        if target_arg.startswith("@"):
            username = target_arg.lstrip("@")
            user = conn.execute(
                "SELECT user_id, username, first_name, last_name FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if not user:
                await update.message.reply_text("User not found for that username.")
                return
            user_id = user[0]
        else:
            user_id = update.effective_user.id

        if target_arg.startswith("@"):
            tasks_list = fetch_tasks_across_threads(
                conn, user_id, update.effective_chat.id, date_text
            )
        else:
            tasks_list = fetch_tasks(
                conn,
                user_id,
                update.effective_chat.id,
                thread_id,
                date_text,
            )
        if not tasks_list:
            await update.message.reply_text(
                "No tasks saved for that date. Use /tasks to add tasks first."
            )
            return
        if target_arg.startswith("@"):
            if is_after_deadline(task_date, deadline_time):
                all_done = all(task["completed"] == 1 for task in tasks_list)
                result = DailyResult(date_text, 1 if all_done else 0, 0 if all_done else 1)
            else:
                result = None
        else:
            result = evaluate_daily_result(
                conn,
                user_id,
                update.effective_chat.id,
                thread_id,
                task_date,
                deadline_time,
            )

    summary_lines = [
        f"Tasks for {date_text}:",
        format_tasks_with_links(
            tasks_list, update.effective_chat, show_thread=target_arg.startswith("@")
        ),
    ]
    if result:
        outcome = "GOAL ✅" if result.goal == 1 else "PENALTY ❌"
        summary_lines.append(f"Result: {outcome}")
    else:
        summary_lines.append("Result: pending (deadline not reached).")

    await update.message.reply_text("\n".join(summary_lines))


async def score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        update_user_profile(conn, update.effective_user)
        thread_id = get_thread_id(update)
        deadline_text = get_chat_deadline(conn, update.effective_chat.id, thread_id)
        if deadline_text:
            deadline_time = parse_time(deadline_text)
            task_dates = conn.execute(
                """
                SELECT DISTINCT date FROM tasks
                WHERE user_id = ? AND chat_id = ? AND thread_id = ?
                """,
                (update.effective_user.id, update.effective_chat.id, thread_id),
            ).fetchall()
            for (date_text,) in task_dates:
                evaluate_daily_result(
                    conn,
                    update.effective_user.id,
                    update.effective_chat.id,
                    thread_id,
                    parse_date(date_text),
                    deadline_time,
                )

        results = fetch_results(
            conn,
            update.effective_user.id,
            update.effective_chat.id,
            thread_id,
        )

    goals = sum(result.goal for result in results)
    penalties = sum(result.penalty for result in results)

    with sqlite3.connect(DB_PATH) as conn:
        goal_adjust, penalty_adjust = fetch_adjustment_totals(
            conn, update.effective_user.id, update.effective_chat.id
        )

    total_goals = goals + goal_adjust
    total_penalties = penalties + penalty_adjust
    await update.message.reply_text(
        f"Total: {total_goals} GOAL(s), {total_penalties} PENALTY(ies)."
    )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        deadline_text = get_chat_deadline(conn, update.effective_chat.id, DEFAULT_THREAD_ID)
        if deadline_text:
            deadline_time = parse_time(deadline_text)
            task_rows = conn.execute(
                """
                SELECT DISTINCT user_id, thread_id, date
                FROM tasks
                WHERE chat_id = ?
                """,
                (update.effective_chat.id,),
            ).fetchall()
            for row in task_rows:
                evaluate_daily_result(
                    conn,
                    row["user_id"],
                    update.effective_chat.id,
                    row["thread_id"],
                    parse_date(row["date"]),
                    deadline_time,
                )
        rows = conn.execute(
            """
            SELECT totals.user_id,
                   totals.goals,
                   totals.penalties,
                   users.username,
                   users.first_name,
                   users.last_name
            FROM (
                SELECT user_id,
                       SUM(goal_value) AS goals,
                       SUM(penalty_value) AS penalties
                FROM (
                    SELECT user_id, goal AS goal_value, penalty AS penalty_value
                    FROM results
                    WHERE chat_id = ?
                    UNION ALL
                    SELECT user_id, goal_delta AS goal_value, penalty_delta AS penalty_value
                    FROM adjustments
                    WHERE chat_id = ?
                ) merged
                GROUP BY user_id
            ) totals
            LEFT JOIN users ON users.user_id = totals.user_id
            ORDER BY totals.goals DESC, totals.penalties ASC, totals.user_id ASC
            """,
            (update.effective_chat.id, update.effective_chat.id),
        ).fetchall()

    if not rows:
        await update.message.reply_text("No results yet. Check back after the deadline.")
        return

    lines = ["Leaderboard (GOALs / PENALTYs):"]
    for idx, row in enumerate(rows, start=1):
        label = format_user_label(row)
        lines.append(f"{idx}. {label} — {row['goals']} / {row['penalties']}")
    await update.message.reply_text("\n".join(lines))


async def add_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage: /addscore @username GOALS PENALTIES [note]\n"
            "Example: /addscore @abdulhamid 2 1 restored after restart"
        )
        return

    member = await context.bot.get_chat_member(
        update.effective_chat.id, update.effective_user.id
    )
    if not user_is_admin(member):
        await update.message.reply_text("Only admins can adjust scores.")
        return

    target, goals_text, penalties_text = context.args[:3]
    note = " ".join(context.args[3:]).strip() if len(context.args) > 3 else None

    if not goals_text.lstrip("-").isdigit() or not penalties_text.lstrip("-").isdigit():
        await update.message.reply_text("GOALS and PENALTIES must be integers.")
        return

    goal_delta = int(goals_text)
    penalty_delta = int(penalties_text)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        update_user_profile(conn, update.effective_user)
        user_id = resolve_target_user_id(conn, target)
        if user_id is None:
            await update.message.reply_text(
                "User not found. Use @username already seen by the bot or numeric user_id."
            )
            return

        conn.execute(
            """
            INSERT INTO adjustments (user_id, chat_id, goal_delta, penalty_delta, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                update.effective_chat.id,
                goal_delta,
                penalty_delta,
                note,
                utc_now().isoformat(),
            ),
        )

        label_row = conn.execute(
            "SELECT user_id, username, first_name, last_name FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        label = format_user_label(label_row) if label_row else f"User {user_id}"

    await update.message.reply_text(
        f"Adjusted {label}: GOAL {goal_delta:+d}, PENALTY {penalty_delta:+d}."
    )


def main() -> None:
    init_db()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is required.")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("setdeadline", set_deadline))
    application.add_handler(CommandHandler("tasks", tasks))
    application.add_handler(CommandHandler("done", done))
    application.add_handler(
        MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/done"), done)
    )
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("score", score))
    application.add_handler(CommandHandler("leaderboard", leaderboard))
    application.add_handler(CommandHandler("addscore", add_score))

    if application.job_queue:
        application.job_queue.run_repeating(
            send_daily_reports,
            interval=REPORT_CHECK_MINUTES * 60,
            first=REPORT_CHECK_MINUTES * 60,
        )
    else:
        LOGGER.warning(
            "JobQueue is unavailable. Install python-telegram-bot[job-queue] to enable daily reports."
        )

    application.run_polling()


if __name__ == "__main__":
    main()
