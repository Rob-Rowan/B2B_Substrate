# 📡 B2B Substrate

**Zero-LLM, High-Precision Manual Outreach Substrate**

B2B Substrate is a local-first Streamlit application for manually
logging B2B leads, deduplicating them against existing records,
tracking them through a strict six-state status lifecycle, and
generating a personalized outreach draft via a lightweight, fully
deterministic Jinja2 interpolation engine.

There is **no LLM provider, no web scraper, and no outbound SMTP
relay** anywhere in this application or its dependency tree. Every
decision — qualification, drafting, and status transition — is made
by a human operator; the software's only job is to prevent duplicate
entries, enforce a legal lifecycle graph, and remove the boilerplate
of writing the same cold-email opener by hand.

---

## Table of Contents

1. [System Architecture Overview](#system-architecture-overview)
2. [Core Capabilities](#core-capabilities)
3. [Lead Lifecycle States](#lead-lifecycle-states)
4. [Local Installation & Setup](#local-installation--setup)
5. [Environment Variables](#environment-variables)
6. [Database Schema Reference](#database-schema-reference)
7. [Operational Workflows](#operational-workflows)
8. [Testing & Verification](#testing--verification)
9. [Code Quality Standards](#code-quality-standards)
10. [Project Structure](#project-structure)
11. [Data Integrity Guarantee](#data-integrity-guarantee)

---

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Streamlit UI (app.py)                          │
│  ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────┐  │
│  │ Manual Ingestion│   │ Cold Triage Desk │   │  Master Ledger   │  │
│  └─────────────────┘   └──────────────────┘   └──────────────────┘  │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │              KPI Ribbon (per-lifecycle-state counts)          │  │
│  └───────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                Lead Service Layer (lead_service.py)                 │
│   create_lead → normalize(email/website) → dedup → default          │
│       QUALIFIED                                                     │
│   transition_lead_status → ALLOWED_TRANSITIONS graph enforcement    │
│   generate_lead_draft → templates_engine.render_draft               │
│   Structured error payloads: 409 / 404 / 400                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│           Draft Interpolation Engine (templates_engine.py)          │
│   extract_first_name(contact_name) + Jinja2 subject/body templates  │
│   Zero LLM calls — pure deterministic string templating             │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                 SQLAlchemy ORM (models.py, database.py)             │
│   Lead / LeadTouch Declarative Base mapped onto EXISTING tables     │
│   Lead.touches <-> LeadTouch.lead (cascade="all, delete-orphan")    │
│   get_engine() / get_session() / init_db() [checkfirst=True no-op]  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│              SQLite (leads.db) — PRE-EXISTING, UNALTERED            │
│   leads (28 cols, UNIQUE domain/verified_email)                     │
│   lead_touches (FK lead_id -> leads.id ON DELETE CASCADE)           │
└─────────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Ingest** — The operator submits the Manual Ingestion form with
   `company_name`, `contact_name`, `website`, `contact_title`,
   `email`, `tech_stack`, and `notes`.
2. **Deduplicate** — `lead_service.create_lead` normalizes `email`
   (lowercase/trim) and `website` (strip scheme, `www.`, and
   path/query) before checking both against every existing lead. A
   collision raises `DuplicateLeadError`, surfaced as a structured
   409 Conflict payload.
3. **Default status** — New leads are always created with
   `status="QUALIFIED"`; there is no automated qualification step.
4. **Triage** — The Cold Triage Desk's lead selector is scoped
   **exclusively** to `QUALIFIED` leads. Selecting one auto-populates
   a personalized pitch draft (contact first name + tech stack) via
   the deterministic Jinja2 template engine, fully editable before
   the operator clicks **Queue Lead**, which persists the edit and
   transitions the lead directly to `QUEUED`.
5. **Ledger** — The Master Ledger provides full search/filter across
   every lead plus a manual status-override control constrained to
   the legal transition graph in `config.ALLOWED_TRANSITIONS`.
6. **Send cap scheduling** — Outreach dispatch itself is intentionally
   manual and operator-paced: because every lead must be individually
   reviewed, drafted, and queued through the Cold Triage Desk before
   it can move to `SENT`, the UI's single-lead-at-a-time workflow acts
   as the daily send cap enforcement mechanism — there is no batch
   "send all queued leads" action, so daily outreach volume is bounded
   by how many leads an operator personally queues and marks `SENT`
   in a session.

---

## Core Capabilities

| Capability | Implementation |
|-----------|---------------|
| **SQLAlchemy ORM layer** | `Lead`/`LeadTouch` Declarative Base models (`models.py`) mapped explicitly onto the pre-existing `leads`/`lead_touches` tables, column for column |
| **Non-destructive schema guard** | `database.init_db()` only ever calls `create_all(checkfirst=True)` — a documented no-op against tables that already exist |
| **One-to-many relationship** | `Lead.touches` <-> `LeadTouch.lead`, `cascade="all, delete-orphan"`, `passive_deletes=True` — mirrors the existing `ON DELETE CASCADE` FK |
| **Manual intake workflow** | A single clean ingestion form (`company_name`, `contact_name`, `website`, `contact_title`, `email`, `tech_stack`, `notes`) — no bulk/JSON/HTTP ingestion path exists |
| **Deduplication logic** | `lead_service.normalize_email`/`normalize_website` canonicalize input before every dedup check and insert; pre-insert lookups against `Lead.verified_email` and `Lead.domain` (both `UNIQUE`) |
| **Template interpolation engine** | `templates_engine.py` — `extract_first_name` + a dedicated `jinja2.Environment(autoescape=False, undefined=StrictUndefined)` rendering `DEFAULT_SUBJECT_TEMPLATE`/`DEFAULT_BODY_TEMPLATE`; zero LLM calls |
| **Daily send cap scheduler** | Enforced structurally: the Cold Triage Desk only ever surfaces and queues one lead at a time, so outbound volume is bounded by deliberate, sequential operator action rather than an unattended batch job |
| **Six-state lifecycle** | `QUALIFIED`, `QUEUED`, `SENT`, `REPLIED`, `DISQUALIFIED`, `ARCHIVED` — enforced by the explicit `ALLOWED_TRANSITIONS` graph; `UNPROCESSED` is never referenced |
| **Structured error payloads** | `DuplicateLeadError` (409), `LeadNotFoundError` (404), `InvalidTransitionError`/`UnknownStatusError` (400) — each carries a serializable `ErrorPayload` dataclass |

---

## Lead Lifecycle States

| State | Meaning | Entered From |
|-------|---------|---------------|
| `QUALIFIED` | Default state for every newly ingested lead; awaiting triage. | New ingestion, or re-qualification from `DISQUALIFIED`/`ARCHIVED`. |
| `QUEUED` | Draft has been reviewed/edited on the Cold Triage Desk and is ready for outreach. | `QUALIFIED` (via **Queue Lead**). |
| `SENT` | Outreach has been dispatched to the lead (dispatch itself is external/manual — this application only records the status). | `QUEUED`. |
| `REPLIED` | The lead has responded. | `SENT`. |
| `DISQUALIFIED` | The lead has been manually ruled out. | `QUALIFIED` or `QUEUED`. |
| `ARCHIVED` | The lead is closed out / no further action expected. | Any of `QUALIFIED`, `QUEUED`, `SENT`, `REPLIED`, `DISQUALIFIED`. |

### Transition Graph

```
QUALIFIED ──► QUEUED ──► SENT ──► REPLIED
    │            │                  │
    ▼            ▼                  ▼
DISQUALIFIED  ARCHIVED           ARCHIVED
    │
    ▼
 QUALIFIED (re-qualify)

ARCHIVED ──► QUALIFIED (re-open)
```

The full transition graph lives in `config.ALLOWED_TRANSITIONS`
(a `dict[str, frozenset[str]]`) and is enforced exclusively by
`lead_service.transition_lead_status` — the single choke point for
every `Lead.status` mutation in the application. `UNPROCESSED` is
never referenced anywhere in this codebase; any pre-existing legacy
rows carrying that (or any other historical) status remain visible in
the Master Ledger as read-only data with no forward transitions
defined.

---

## Local Installation & Setup

### Prerequisites

- **Python 3.11+**
- **pip**

### Windows One-Click Launch

Double-click **`launch.bat`** in the project root. It creates a
`.venv`, installs dependencies from `requirements.txt`, and launches
the app at `http://localhost:8501`, auto-opening your default browser
once the server responds.

### Manual Setup (any OS)

```bash
python -m venv .venv
source .venv/bin/activate      # macOS/Linux
.venv\Scripts\activate         # Windows

pip install -r requirements.txt

streamlit run app.py
```

The app will be available at `http://localhost:8501`.

### Local Database Setup

No manual database provisioning step is required. On first run,
`database.init_db()` calls `Base.metadata.create_all(engine,
checkfirst=True)` against the SQLite file at `DATABASE_PATH` (default:
`<project_root>/leads.db`):

- If `leads.db` **does not exist**, a fresh file is created with the
  full `leads`/`lead_touches` schema defined in `models.py`.
- If `leads.db` **already exists** (the common case — a pre-existing
  production database), `checkfirst=True` makes this call a
  documented, complete no-op: no `ALTER TABLE`, no `DROP TABLE`, no
  row mutation of any kind.

WAL journal mode and `PRAGMA foreign_keys = ON` are applied on every
new connection via a SQLAlchemy `connect` event listener in
`database.get_engine()`, enabling safe concurrent read/write access
under Streamlit's rerun execution model.

---

## Environment Variables

All environment variables are optional; the application runs with
sensible defaults out of the box. Copy `.env.example` to `.env` to
override any of them.

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `<project_root>/leads.db` | Absolute or relative path to the SQLite database file. Resolved and made absolute at import time in `config.py`. |

`.env` is loaded automatically via `python-dotenv` at the top of
`config.py`. There are **no** SMTP, LLM/API-key, or scraper-related
variables anywhere in this application's configuration surface.

---

## Database Schema Reference

The `leads` and `lead_touches` tables are treated as **externally
owned, pre-existing schema** — the application only ever performs
`SELECT`/`INSERT`/`UPDATE`/`DELETE` through the ORM, never DDL that
could reshape or wipe them.

### `leads` (28 columns, mapped by `models.Lead`)

Actively used by the current application: `id`, `company_name`,
`domain` (**UNIQUE**), `verified_email` (**UNIQUE**), `contact_name`,
`title`, `tech_stack`, `status`, `custom_subject`, `custom_pitch`,
`created_at`, `updated_at`, `notes`.

Retained for backward read/write compatibility with historical rows
only (never written by new code): `website_text`, `sanitized_text`,
`qualification_verdict`, `reasoning`, `search_helpers`,
`email_1_sent_at`, `followup_1_due_date`, `followup_1_sent_at`,
`followup_2_due_date`, `breakup_sent_at`, `replied_at`, `skipped_at`,
`bounced_at`, `email_candidates`, `mailbox_status`.

### `lead_touches` (8 columns, mapped by `models.LeadTouch`)

`id`, `lead_id` (FK → `leads.id`, `ON DELETE CASCADE`), `touch_type`,
`subject`, `body`, `status`, `sent_at`, `created_at`.

The `Lead.touches` <-> `LeadTouch.lead` relationship is declared with
`cascade="all, delete-orphan"` and `passive_deletes=True`, mirroring
the existing foreign-key-level `ON DELETE CASCADE` constraint.

---

## Operational Workflows

### Workflow 1 — Manual Intake

1. Navigate to the **📥 Manual Ingestion** tab.
2. Fill in `Company Name` (required), plus any of `Contact Name`,
   `Website`, `Contact Title`, `Email`, `Tech Stack`, `Notes`.
3. Click **Add Lead**.
   - On success, the lead is created with `status="QUALIFIED"`.
   - On a duplicate `email`/`website`, a 409 Conflict payload
     (`DuplicateLeadError`) is rendered with the offending field and
     value.

### Workflow 2 — Cold Triage Desk

1. Navigate to the **❄️ Cold Triage Desk** tab.
2. Select a lead from the dropdown — scoped **exclusively** to leads
   with `status == "QUALIFIED"`.
3. The subject/body pitch draft auto-populates via
   `templates_engine.render_draft` (contact first name + tech stack
   interpolation) the first time that lead is selected in the
   session. Edit it freely — it stays fully editable.
4. Optionally click **Regenerate Draft** to re-render the default
   template, discarding in-progress edits.
5. Click **Queue Lead** to persist the edited draft onto the lead row
   and transition it directly to `QUEUED` — the only lifecycle
   mutation available on this desk.

### Workflow 3 — Master Ledger

1. Navigate to the **📒 Master Ledger** tab.
2. Search/filter across all leads by company, domain, email, or
   contact name, and optionally by status.
3. Select a lead and apply a manual status override — only legally
   allowed target statuses (per `ALLOWED_TRANSITIONS`) are ever
   offered; legacy/historical statuses outside the six-state set show
   a "no forward transitions defined" notice instead.

---

## Testing & Verification

Run the deterministic backend verification suite:

```bash
python verify_backend.py
```

This suite runs **exclusively against an isolated temporary SQLite
database** created via `tempfile` — it never opens the production
`leads.db`. It verifies:

- Website/email normalization and default `QUALIFIED` status on
  ingestion.
- Deduplication rejection (409) on duplicate `email` and duplicate
  `website`.
- Legal (`QUALIFIED → QUEUED`) and illegal (`QUEUED → REPLIED`)
  status transitions.
- Rejection of unknown/legacy statuses (e.g. `UNPROCESSED`).
- The six-state lifecycle set is exact, with `UNPROCESSED` absent from
  every transition target.
- First-name extraction and Jinja2 draft rendering/persistence.
- The `Lead.touches` <-> `LeadTouch.lead` cascade delete-orphan
  relationship.

Additional static checks used during development:

```bash
python -m py_compile app.py config.py database.py lead_service.py models.py templates_engine.py verify_backend.py
python -m flake8 --max-line-length=79 app.py config.py database.py lead_service.py models.py templates_engine.py verify_backend.py
python -m pydocstyle --convention=google app.py config.py database.py lead_service.py models.py templates_engine.py verify_backend.py
```

All three currently report zero violations across the entire backend.

---

## Code Quality Standards

Every module in this codebase adheres to the following, verified via
the commands above:

- **Strict PEP 8 compliance** — zero `flake8` violations at a
  79-character line length (formatting, whitespace, unused imports,
  naming conventions).
- **Full type-hint coverage** — every function signature, dataclass
  field, and ORM column uses explicit `Mapped[...]`/`Optional[...]`/
  `str | None`-style type hints.
- **Google-style docstrings everywhere** — every module, class,
  function, and method documents `Args`, `Returns`, and `Raises` where
  applicable, verified with zero `pydocstyle --convention=google`
  violations.
- **Single choke points for mutation** — `lead_service.create_lead`
  is the only path that inserts a `Lead`; `transition_lead_status` is
  the only path that mutates `Lead.status`.

---

## Project Structure

```
B2B_Substrate/
├── .env                    # Local environment overrides (git-ignored)
├── .env.example            # Environment variable template
├── .gitignore
├── .streamlit/
│   └── config.toml         # Streamlit theme + server config
├── launch.bat              # Windows one-click launcher (venv + deps + run)
├── memory-bank/            # Project documentation (git-ignored)
│   ├── projectbrief.md
│   ├── productContext.md
│   ├── activeContext.md
│   ├── systemPatterns.md
│   ├── techContext.md
│   └── progress.md
├── app.py                  # Streamlit UI (3 tabs + KPI ribbon)
├── config.py                # DB path + 6-state lifecycle + transition graph
├── database.py              # SQLAlchemy engine/session/init_db
├── models.py                 # Lead / LeadTouch Declarative Base models
├── lead_service.py           # Ingestion, dedup, lifecycle, draft service layer
├── templates_engine.py       # Jinja2 draft interpolation engine
├── requirements.txt
├── verify_backend.py         # Deterministic backend verification (temp DB only)
└── leads.db                  # Pre-existing SQLite database (never altered)
```

---

## Data Integrity Guarantee

This application operates under a strict **zero-data-loss**
constraint against the production `leads.db`:

- `database.init_db()` is intentionally limited to
  `Base.metadata.create_all(engine, checkfirst=True)`, which
  SQLAlchemy documents as a strict "create if absent" — a complete
  no-op for tables that already exist.
- No `ALTER TABLE`, `DROP TABLE`, or destructive migration is ever
  executed by any code path in this repository.
- `models.py` maps every column of the existing `leads` (28 columns)
  and `lead_touches` (8 columns) tables exactly as verified via
  `PRAGMA table_info`, so the ORM layer requires zero schema changes
  to operate against the pre-existing production database.
- Legacy rows created by prior pipeline iterations (carrying
  historical status values such as `UNPROCESSED`) remain fully
  readable through the ORM and visible in the Master Ledger, but are
  never rewritten by current application logic.

---

## License

Proprietary — All rights reserved.

---

*B2B Substrate — Built for Rob Rowan. Zero-LLM, high-precision manual
outreach substrate.*
