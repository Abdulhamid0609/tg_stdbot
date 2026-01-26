# Carpe Diem Telegram Bot

This repository contains a simple Telegram bot for tracking daily tasks, proofs, and scoring (GOAL or PENALTY) based on whether all tasks are completed before a daily deadline.

## Features
- Store daily task lists per participant.
- Record proofs for each task.
- Enforce a per-user daily deadline.
- Automatically award **+1 GOAL** when all tasks are done before the deadline.
- Automatically award **+1 PENALTY** when at least one task is missing after the deadline.

## Commands
- `/start` – Introduction and quick help.
- `/help` – Command list.
- `/setdeadline HH:MM` – Set a daily deadline time (24h, UTC). Admin-only in groups and applies to all topics.
- `/tasks [YYYY-MM-DD]` followed by new lines for each task (date optional = today). Example:
  ```
  /tasks 2026-01-23
  Reading a book
  Running 2 kms
  Listening podcast
  ```
- `/done [YYYY-MM-DD] TASK_NUMBER proof text` – Mark a task done with proof or attach a photo and reply with `/done` (rejected after the deadline).
- `/status [YYYY-MM-DD]` – Show task completion status and daily result.
- `/status @nickname` – (Admin only) View another user’s status for today.
- `/status all` – (Admin only) View all users’ status for today.
- `/score` – Show total goals and penalties.

## Setup
1. Create a Telegram bot and get a token.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the bot:
   ```bash
   export TELEGRAM_BOT_TOKEN="<your-token>"
   python main.py
   ```

## Notes
- Deadlines are interpreted in **UTC** and apply to everyone in the same group.
- Daily results are calculated after the deadline when you request `/status` or `/score`.
- The bot can be added to groups and topic groups; it tracks tasks per user inside each topic.
- After the deadline passes, the bot posts a daily score summary in the group for that date.
- Status output shows proof message links instead of full proof text.
