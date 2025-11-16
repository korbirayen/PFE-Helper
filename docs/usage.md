# PFE Aggregator Bot – Detailed Usage

This document complements the top-level `README.md` with more detailed notes
about inputs, filtering, fitness ranking, outputs, and the tracker.

---

## 1. Inputs and data sources

- **Companies CSV** (fitness reference):
  - Preferred: `/mnt/data/PFE 2026 copy backup - pfeList.csv`
  - Fallback: `data/companies.csv`

  The file is expected to contain:

  - A company name column (e.g. `Company`, `Entreprise`, `Societe`, etc.)
  - `Fitness Category` column with values like `High`, `Medium`, `Low`.

  The script normalizes company names to lowercase and tokenizes them to
  compute a simple Jaccard similarity for fuzzy matching.

- **PFE book PDF**:

  - Default: `/mnt/data/Opportunités stages PFE- Healio_Perspectives.pdf`
  - Parsed with `pdfplumber` (preferred) or `PyPDF2` fallback.

  Any line containing `PFE`, `projet`, or `stage` (case-insensitive) is
  considered part of a PFE description. Consecutive lines are grouped into
  one project entry.

- **CV PDF**:

  - Default: `/mnt/data/Rayen Korbi.pdf`

  Used only to auto-fill:

  - `my_name`
  - `my_email`
  - `my_phone`

  If extraction fails, templates use `{MY_NAME}`, `{MY_EMAIL}`,
  `{MY_PHONE}` and the script logs a warning.

---

## 2. Scrapers

Web scrapers live in `scripts/scrapers.py` and support:

- `https://www.pfebook.com/`
- `https://pfebooks.com/`
- `https://hi-interns.com/internships`
- `https://itgate-group.com/catalogue-pfe/`
- `https://rh.medianet.tn/Fr/stages-pfe-2026_11_50`
- plus a generic fallback scraper.

Each scraper:

- Uses a readable `User-Agent` and timeouts
- Retries once on failure
- Logs blocked/errored URLs to `data/link_statuses.csv`
- Returns entries with:
  - `title`
  - `company`
  - `link`
  - `description`
  - `contact_email` (best-effort; may be empty)
  - `source_url`
  - `date_scraped` (UTC date)

> The HTML structure of these sites may change. The scrapers are written to be
> robust but conservative: if selectors no longer match, you will still see
> entries from the generic scraper when possible.

---

## 3. Fitness, normalization, and de-duplication

After scraping and PDF parsing, `scripts/aggregator_bot.py`:

1. **Normalizes**:
   - Trims and lowercases company names
   - Ensures text fields are strings (no NaN)

2. **Fitness matching** (via `utils.match_company_fitness`):
   - Computes a token-based Jaccard similarity between each project company
     and each company in the CSV.
   - Picks the best match; stores:
     - `fitness` (from CSV `Fitness Category`)
     - `csv_company_match`
     - `fitness_match_score` (float 0-1)
     - `fitness_match_approx` (True if names not exactly equal)

3. **De-duplicates** (via `deduplicate`):
   - Creates `project_id` as a slug of `(title, company)`
   - Sorts by `fitness_match_score` descending
   - Drops duplicates on `(title, company)` keeping the best row

4. **Filters** (via `filter_by_fitness_and_date`):
   - `--fitness "High,Medium"` keeps only those fitness values
   - `--since-days N` keeps entries with `date_scraped >= now - N days`

---

## 4. Outputs

### 4.1 Aggregated CSV

`--save-csv` writes `data/aggregated_projects.csv` containing all selected
projects after dedup/filtering. Use `--force` to overwrite an existing file.

### 4.2 Email drafts

`--generate-emails` creates text files in `emails/`:

- Filename pattern: `<YYYY-MM-DD>_<project_slug>.txt`
- Each file contains:
  - `# French version` + FR body
  - `# English version` + EN body

Keys in the template include:

- `project.title`
- `project.company`
- `project.link` / `project.source_url`
- `project.fitness`
- `project.fitness_match_approx` (adds a note when match is approximate)

### 4.3 Telegram

`--post-telegram` posts a concise message per project:

```text
PFE: <title> — <company>
Fitness: <fitness> (approx company match)
Link: <link or source_url>
```

Requires in `.env`:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 4.4 GitHub issues

`--create-issues` creates issues in `GITHUB_REPO`:

- Title: `PFE: <title> — <company>`
- Body: rendered by `scripts/templates.py` (includes description, link,
  fitness, and approximate-match note if relevant).

Requires in `.env`:

- `GITHUB_TOKEN`
- `GITHUB_REPO` (e.g. `yourusername/pfe-opportunities`)

---

## 5. Tracker

`data/tracker.csv` is automatically created (if missing) and a row is appended
per project processed in the current run, with columns:

```text
date_added, project_id, title, company, fitness, pfe_link, contact_email,
posted_telegram, github_issue_url, email_draft, last_action, status, notes
```

- `project_id` is the slug used in filenames and can be used for manual
  tracking.
- `notes` currently stores `fitness_match_approx=True/False`.

You can update the `status` column via CLI:

```bash
python scripts/aggregator_bot.py --update-status my-project-slug:Contacted
```

This reads `tracker.csv`, updates the row with `project_id == my-project-slug`,
sets `status` and `last_action`, and saves it back.

---

## 6. Typical daily flow

1. Pull new PFE postings (web + PDF), rank by fitness, and create drafts:

   ```bash
   python scripts/aggregator_bot.py \
     --top 30 \
     --fitness High,Medium \
     --generate-emails \
     --save-csv
   ```

2. Check `emails/` and `data/aggregated_projects.csv`.

3. Optionally post top High fitness offers from the last week to Telegram:

   ```bash
   python scripts/aggregator_bot.py \
     --post-telegram \
     --fitness High \
     --since-days 7
   ```

4. Optionally create GitHub issues for remaining interesting offers.

5. Update tracker statuses as you contact companies:

   ```bash
   python scripts/aggregator_bot.py --update-status some-project-id:Contacted
   ```

