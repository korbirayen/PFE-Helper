# PFE Aggregator Bot

Python-based bot to aggregate PFE / project / internship postings from:

- pfebook.com / pfebooks.com
- hi-interns.com
- itgate-group.com
- rh.medianet.tn
- PFE book PDFs (e.g. `/mnt/data/Opportunités stages PFE- Healio_Perspectives.pdf`)

It normalizes and ranks results using your master companies CSV (with `Fitness Category`), de-duplicates projects, and can:

- Generate email drafts (FR + EN) in `emails/`
- Post concise messages to a Telegram chat
- Create GitHub issues in a target repo
- Save all aggregated projects to `data/aggregated_projects.csv`

The bot is designed to run directly from VSCode with minimal setup.

---

## Setup

1. **Create and activate a virtualenv**

   ```bash
   python -m venv .venv
   # Linux/macOS
   source .venv/bin/activate
   # Windows (PowerShell)
   .venv\\Scripts\\Activate.ps1
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Environment variables**

   Copy `.env.example` to `.env` and fill in values as needed:

   ```env
   TELEGRAM_BOT_TOKEN=
   TELEGRAM_CHAT_ID=

   GITHUB_TOKEN=
   GITHUB_REPO=yourusername/your-pfe-repo
   ```

4. **Input files**

- Companies CSV (master list with `Fitness Category`):
  - Preferred: `/mnt/data/PFE 2026 copy backup - pfeList.csv`
  - Fallback: `data/companies.csv`

- PFE book PDF: `/mnt/data/Opportunités stages PFE- Healio_Perspectives.pdf`
- CV PDF: `/mnt/data/Rayen Korbi.pdf` (used to auto-fill name/email/phone in templates)

The code will automatically use the `/mnt/data/...` paths when present.

---

## Core CLI

Main entrypoint:

```bash
python scripts/aggregator_bot.py [options]
``

Key options:

- `--top N` – limit number of projects
- `--fitness "High,Medium"` – filter by fitness levels
- `--since-days N` – only keep projects from last N days
- `--generate-emails` – write FR/EN email drafts to `emails/`
- `--post-telegram` – send messages to Telegram
- `--create-issues` – create GitHub issues
- `--save-csv` – write `data/aggregated_projects.csv`
- `--force` – overwrite `aggregated_projects.csv` if it already exists
- `--debug` – verbose logging
- `--update-status project_id:NewStatus` – update a row in `data/tracker.csv`

`data/tracker.csv` is automatically created and updated and contains:

```text
date_added, project_id, title, company, fitness, pfe_link, contact_email,
posted_telegram, github_issue_url, email_draft, last_action, status, notes
```

---

## Example workflows

### 1) Generate email drafts and save CSV

```bash
python scripts/aggregator_bot.py \
  --top 30 \
  --fitness High,Medium \
  --generate-emails \
  --save-csv
```

Email drafts will appear as files like `emails/2025-01-01_<slug>.txt` with FR and EN versions.

### 2) Post top High fitness projects to Telegram (last 7 days)

```bash
python scripts/aggregator_bot.py \
  --post-telegram \
  --fitness High \
  --since-days 7
```

### 3) Create GitHub issues for selected projects

```bash
python scripts/aggregator_bot.py \
  --create-issues \
  --fitness High,Medium \
  --top 20
```

---

## VSCode tasks

Two ready-to-use tasks are defined in `.vscode/tasks.json`:

- **Run aggregator: emails + csv** – equivalent to:

  ```bash
  python scripts/aggregator_bot.py --top 30 --fitness High,Medium --generate-emails --save-csv
  ```

- **Run aggregator: Telegram High last 7 days** – equivalent to:

  ```bash
  python scripts/aggregator_bot.py --post-telegram --fitness High --since-days 7
  ```

Run them from the VSCode command palette: **Tasks: Run Task**.

---

## Notes

- All outputs indicate the matched CSV company and whether the match was approximate.
- If CV parsing fails, email drafts fall back to `{MY_NAME}`, `{MY_EMAIL}`, `{MY_PHONE}` placeholders with a warning in the logs.
- `data/link_statuses.csv` records any URLs that failed or were blocked while scraping.