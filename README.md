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
- `/setdeadline HH:MM` – Set a daily deadline time (24h, UTC).
- `/tasks YYYY-MM-DD` followed by new lines for each task. Example:
  ```
  /tasks 2026-01-23
  Reading a book
  Running 2 kms
  Listening podcast
  ```
- `/done YYYY-MM-DD TASK_NUMBER proof text` – Mark a task done with proof or a photo (rejected after the deadline).
- `/status YYYY-MM-DD` – Show task completion status and daily result.
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
- Deadlines are interpreted in **UTC**.
- Daily results are calculated after the deadline when you request `/status` or `/score`.
- The bot can be added to groups; it tracks tasks per user in that chat.
