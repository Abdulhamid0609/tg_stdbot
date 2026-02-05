# Carpe Diem Telegram Bot

This repository contains a simple Telegram bot for tracking daily tasks, proofs, and scoring (GOAL or PENALTY) based on whether all tasks are completed before a daily deadline.

## Features
- Store daily task lists per participant.
- Record proofs for each task.
- Enforce a per-group daily deadline.
- Automatically award **+1 GOAL** when all tasks are done before the deadline.
- Automatically award **+1 PENALTY** when at least one task is missing after the deadline.

## Commands
- `/start` – Introduction and quick help.
- `/help` – Command list.
- `/setdeadline HH:MM` – Set the group deadline once (24h, UTC). Admin-only in groups and applies to all topics.
- `/tasks [YYYY-MM-DD]` followed by new lines for each task (date optional = today). Example:
  ```
  /tasks 2026-01-23
  Reading a book
  Running 2 kms
  Listening podcast
  ```
- `/done [YYYY-MM-DD] TASK_NUMBER proof text` – Mark a task done with proof or attach a photo and reply with `/done` (rejected after the deadline).
- `/status [YYYY-MM-DD]` – Show task completion status and daily result.
- `/status @nickname [YYYY-MM-DD]` – (Admin only) View another user’s status across all topics.
- `/status all [YYYY-MM-DD]` – (Admin only) View all users’ status across all topics.
- `/score` – Show total goals and penalties.
- `/leaderboard` – Show GOALs and PENALTYs for all users in the group across all topics.

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

## Free Hosting (Always-On via Google Cloud Always Free)
If you need a free always-on cloud option, use a Google Cloud **Always Free** `e2-micro` VM (selected regions). This is separate from the 90-day trial credits.

1. Create a Google Cloud account and project.
2. In **Compute Engine**, create an Ubuntu `e2-micro` VM in an Always Free region (for example `us-west1`, `us-central1`, or `us-east1`).
3. SSH into the VM and install Python 3.11+ and Git.
4. Clone this repository to the VM.
5. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
6. Set environment variables:
   ```bash
   export TELEGRAM_BOT_TOKEN="<your-token>"
   export CARPE_DIEM_DB="$HOME/carpe_diem.sqlite3"
   ```
7. Create a `systemd` service for auto-start and auto-restart.
8. Put the same environment variables in the `systemd` service file so the bot survives reboot.

This provides a practical free 24/7 cloud deployment path when used within Always Free limits.

## Notes
- Deadlines are interpreted in **UTC** and apply to everyone in the same group.
- Daily results are calculated after the deadline when you request `/status` or `/score`.
- The bot can be added to groups and topic groups; it tracks tasks per user inside each topic, while admin status/leaderboard commands aggregate across topics.
- After the deadline passes, the bot posts a daily score summary in the group for that date.
- Status output shows proof message links instead of full proof text.
