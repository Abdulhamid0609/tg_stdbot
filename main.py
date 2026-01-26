import datetime as dt
import os
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

DB_PATH = os.environ.get("CARPE_DIEM_DB", "carpe_diem.sqlite3")


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
                deadline_time TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                task_index INTEGER NOT NULL,
                description TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                proof TEXT,
                proof_file_id TEXT,
                UNIQUE(user_id, chat_id, date, task_index)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                goal INTEGER NOT NULL DEFAULT 0,
                penalty INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, chat_id, date)
            )
            """
        )
        ensure_column(conn, "tasks", "chat_id", "INTEGER", "user_id")
        ensure_column(conn, "tasks", "proof_file_id", "TEXT")
        ensure_column(conn, "results", "chat_id", "INTEGER", "user_id")


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


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def parse_time(value: str) -> dt.time:
    return dt.time.fromisoformat(value)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def deadline_datetime(target_date: dt.date, deadline_time: dt.time) -> dt.datetime:
    return dt.datetime.combine(target_date, deadline_time, tzinfo=dt.timezone.utc)


def is_after_deadline(target_date: dt.date, deadline_time: dt.time) -> bool:
    return utc_now() > deadline_datetime(target_date, deadline_time)


def fetch_tasks(
    conn: sqlite3.Connection, user_id: int, chat_id: int, date: str
) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM tasks WHERE user_id = ? AND chat_id = ? AND date = ? ORDER BY task_index",
        (user_id, chat_id, date),
    ).fetchall()


def fetch_results(conn: sqlite3.Connection, user_id: int, chat_id: int) -> list[DailyResult]:
    rows = conn.execute(
        "SELECT date, goal, penalty FROM results WHERE user_id = ? AND chat_id = ? ORDER BY date",
        (user_id, chat_id),
    ).fetchall()
    return [DailyResult(date=row[0], goal=row[1], penalty=row[2]) for row in rows]


def evaluate_daily_result(
    conn: sqlite3.Connection,
    user_id: int,
    chat_id: int,
    date: dt.date,
    deadline_time: dt.time,
) -> DailyResult | None:
    if not is_after_deadline(date, deadline_time):
        return None

    existing = conn.execute(
        "SELECT date, goal, penalty FROM results WHERE user_id = ? AND chat_id = ? AND date = ?",
        (user_id, chat_id, date.isoformat()),
    ).fetchone()
    if existing:
        return DailyResult(date=existing[0], goal=existing[1], penalty=existing[2])

    tasks = fetch_tasks(conn, user_id, chat_id, date.isoformat())
    if not tasks:
        return None

    all_done = all(task["completed"] == 1 for task in tasks)
    goal = 1 if all_done else 0
    penalty = 0 if all_done else 1

    conn.execute(
        "INSERT INTO results (user_id, chat_id, date, goal, penalty) VALUES (?, ?, ?, ?, ?)",
        (user_id, chat_id, date.isoformat(), goal, penalty),
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


def help_text() -> str:
    return (
        "Commands:\n"
        "/start - Welcome message\n"
        "/help - Show this help\n"
        "/setdeadline HH:MM (UTC) - Set your daily deadline\n"
        "/tasks YYYY-MM-DD + tasks on new lines - Save daily tasks\n"
        "/done YYYY-MM-DD TASK_NUMBER proof - Mark a task done (photos supported)\n"
        "/status YYYY-MM-DD - Show task status and result\n"
        "/score - Show total goals and penalties"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = (
        "Welcome to Carpe Diem!\n"
        "Use /setdeadline HH:MM (UTC) to set your daily deadline.\n"
        "Send tasks with /tasks YYYY-MM-DD followed by each task on a new line.\n"
        "Mark tasks done with /done YYYY-MM-DD TASK_NUMBER proof text.\n"
        "Use /help for the full command list."
    )
    await update.message.reply_text(message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(help_text())


async def set_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /setdeadline HH:MM")
        return

    try:
        deadline_time = parse_time(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid time. Use HH:MM (24h).")
        return

    with sqlite3.connect(DB_PATH) as conn:
        set_deadline_time(conn, update.effective_user.id, deadline_time.isoformat())

    await update.message.reply_text(
        f"Deadline set to {deadline_time.strftime('%H:%M')} UTC."
    )


async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /tasks YYYY-MM-DD followed by tasks on new lines.")
        return

    date_text = context.args[0]
    try:
        task_date = parse_date(date_text)
    except ValueError:
        await update.message.reply_text("Invalid date. Use YYYY-MM-DD.")
        return

    raw_text = update.message.text or ""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    task_lines = lines[1:] if lines and lines[0].startswith("/tasks") else lines

    if not task_lines:
        await update.message.reply_text("Please provide at least one task on a new line.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM tasks WHERE user_id = ? AND chat_id = ? AND date = ?",
            (update.effective_user.id, update.effective_chat.id, date_text),
        )
        for index, task in enumerate(task_lines, start=1):
            conn.execute(
                "INSERT INTO tasks (user_id, chat_id, date, task_index, description)"
                " VALUES (?, ?, ?, ?, ?)",
                (update.effective_user.id, update.effective_chat.id, date_text, index, task),
            )

    await update.message.reply_text(
        f"Saved {len(task_lines)} task(s) for {date_text}."
    )


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /done YYYY-MM-DD TASK_NUMBER proof text")
        return

    date_text = context.args[0]
    task_number_text = context.args[1]
    proof = " ".join(context.args[2:]).strip() if len(context.args) > 2 else ""

    try:
        task_date = parse_date(date_text)
    except ValueError:
        await update.message.reply_text("Invalid date. Use YYYY-MM-DD.")
        return

    if not task_number_text.isdigit():
        await update.message.reply_text("Task number must be numeric.")
        return

    task_number = int(task_number_text)
    photo_file_id = (
        update.message.photo[-1].file_id
        if update.message and update.message.photo
        else None
    )

    with sqlite3.connect(DB_PATH) as conn:
        deadline_text = get_deadline_time(conn, update.effective_user.id)
        if not deadline_text:
            await update.message.reply_text("Set a deadline first with /setdeadline HH:MM.")
            return

        deadline_time = parse_time(deadline_text)
        if is_after_deadline(task_date, deadline_time):
            await update.message.reply_text(
                "Deadline passed for that date. Proofs can no longer be accepted."
            )
            return

        row = conn.execute(
            "SELECT id FROM tasks WHERE user_id = ? AND chat_id = ? AND date = ? AND task_index = ?",
            (update.effective_user.id, update.effective_chat.id, date_text, task_number),
        ).fetchone()
        if not row:
            await update.message.reply_text("Task not found for that date.")
            return

        conn.execute(
            "UPDATE tasks SET completed = 1, proof = ?, proof_file_id = ? WHERE id = ?",
            (proof if proof else None, photo_file_id, row[0]),
        )

    await update.message.reply_text(
        f"Marked task {task_number} done for {date_text}."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /status YYYY-MM-DD")
        return

    date_text = context.args[0]
    try:
        task_date = parse_date(date_text)
    except ValueError:
        await update.message.reply_text("Invalid date. Use YYYY-MM-DD.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        deadline_text = get_deadline_time(conn, update.effective_user.id)
        if not deadline_text:
            await update.message.reply_text("Set a deadline first with /setdeadline HH:MM.")
            return

        deadline_time = parse_time(deadline_text)
        tasks_list = fetch_tasks(
            conn, update.effective_user.id, update.effective_chat.id, date_text
        )
        if not tasks_list:
            await update.message.reply_text("No tasks saved for that date.")
            return

        result = evaluate_daily_result(
            conn,
            update.effective_user.id,
            update.effective_chat.id,
            task_date,
            deadline_time,
        )

    summary_lines = [f"Tasks for {date_text}:", format_tasks(tasks_list)]
    if result:
        outcome = "GOAL ✅" if result.goal == 1 else "PENALTY ❌"
        summary_lines.append(f"Result: {outcome}")
    else:
        summary_lines.append("Result: pending (deadline not reached).")

    await update.message.reply_text("\n".join(summary_lines))


async def score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        deadline_text = get_deadline_time(conn, update.effective_user.id)
        if deadline_text:
            deadline_time = parse_time(deadline_text)
            task_dates = conn.execute(
                "SELECT DISTINCT date FROM tasks WHERE user_id = ? AND chat_id = ?",
                (update.effective_user.id, update.effective_chat.id),
            ).fetchall()
            for (date_text,) in task_dates:
                evaluate_daily_result(
                    conn,
                    update.effective_user.id,
                    update.effective_chat.id,
                    parse_date(date_text),
                    deadline_time,
                )

        results = fetch_results(conn, update.effective_user.id, update.effective_chat.id)

    goals = sum(result.goal for result in results)
    penalties = sum(result.penalty for result in results)
    await update.message.reply_text(
        f"Total: {goals} GOAL(s), {penalties} PENALTY(ies)."
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
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("score", score))

    application.run_polling()


if __name__ == "__main__":
    main()
