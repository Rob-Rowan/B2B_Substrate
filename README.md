# 📡 B2B Substrate

**Manual Lead Triage & Status Lifecycle Tracker**

B2B Substrate is a local-first Streamlit application for manually logging B2B leads, deduplicating them against existing records, tracking them through a strict six-state status lifecycle, and generating a personalized outreach draft via a lightweight Jinja2 interpolation engine.

---

## Table of Contents

1. [System Architecture Overview](#system-architecture-overview)
2. [Core Capabilities](#core-capabilities)
3. [Local Installation & Setup](#local-installation--setup)
4. [Database Schema Reference](#database-schema-reference)
5. [Status Lifecycle](#status-lifecycle)
6. [Operational Workflows](#operational-workflows)
7. [Testing & Verification](#testing--verification)
8. [Project Structure](#project-structure)
9. [Data Integrity Guarantee](#data-integrity-guarantee)

---

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Streamlit UI (app.py)                          │
│  ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────┐  │
│  │ Manual Ingestion │   │ Cold Triage Desk │   │  Master Ledger   │  │
│  └─────────────────┘   └──────────────────┘   └──────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                Lead Service Layer (lead_service.py)                 │
│   create_lead → dedup(email/website) → default QUALIFIED            │
│   transition_lead_status → ALLOWED_TRANSITIONS graph enforcement    │
│   generate_lead_draft → templates_engine.render_draft                │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│           Draft Interpolation Engine (templates_engine.py)          │
│   extract_first_name(contact_name) + Jinja2 subject/body templates  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                 SQLAlchemy ORM (models.py, database.py)             │
│   Lead / LeadTouch Declarative Base mapped onto EXISTING tables     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│              SQLite (leads.db) — PRE-EXISTING, UNALTERED            │
└─────────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Ingest** — The user submits the Manual Ingestion form.
2. **Deduplicate** — `email`/`website` are normalized and checked against every existing lead; a duplicate raises a structured 409 Conflict.
3. **Default status** — New leads are always created with status `QUALIFIED`.
4. **Triage** — The Cold Triage Desk dropdown lists only `QUALIFIED` leads; selecting one auto-populates a personalized pitch draft (first name + tech stack) that stays fully editable; clicking **Queue Lead** persists the edit and moves the lead straight to `QUEUED`.
5. **Ledger** — The Master Ledger provides full search/filter and a manual status-override control constrained to the legal transition graph.

---

## Core Capabilities

| Capability | Implementation |
|-----------|---------------|
| **SQLAlchemy ORM** | `Lead`/`LeadTouch` Declarative Base models mapped explicitly onto the pre-existing `leads`/`lead_touches` tables |
| **Non-destructive schema guard** | `init_db()` only ever calls `create_all(checkfirst=True)` — a documented no-op against existing tables |
| **One-to-many relationship** | `Lead.touches` <-> `LeadTouch.lead`, `cascade="all, delete-orphan"`, mirrors the existing `ON DELETE CASCADE` FK |
| **Manual ingestion & dedup** | `company_name`, `contact_name`, `website`, `contact_title`, `email`, `tech_stack`, `notes` — pre-insert dedup on `email`/`website` |
| **Six-state lifecycle** | `QUALIFIED`, `QUEUED`, `SENT`, `REPLIED`, `DISQUALIFIED`, `ARCHIVED` — enforced by an explicit transition graph, `UNPROCESSED` never referenced |
| **Draft interpolation** | Jinja2 template rendering keyed on the contact's first name and recorded tech stack — zero LLM calls |
| **Structured error payloads** | `DuplicateLeadError` (409), `LeadNotFoundError` (404), `InvalidTransitionError`/`UnknownStatusError` (400) |

---

## Local Installation & Setup

### Prerequisites

- **Python 3.11+**
- **pip**

### Dependencies

```
streamlit>=1.36.0
pydantic>=2.7.0
sqlalchemy>=2.0.30
jinja2>=3.1.0
python-dotenv>=1.0.0
```

### Windows One-Click Launch

Double-click **`launch.bat`** in the project root. It creates a `.venv`, installs dependencies, and launches the app at `http://localhost:8501`.

### Manual Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
streamlit run app.py
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `<project_root>/leads.db` | Override the SQLite database file path |

---

## Database Schema Reference

The `leads` and `lead_touches` tables are **pre-existing** and are never altered by this application. `models.py` maps onto them exactly as they exist today; `database.init_db()` performs only an additive, no-op-safe `create_all(checkfirst=True)` schema guard.

### `leads` (28 columns, mapped by `models.Lead`)

Includes `id`, `company_name`, `domain` (UNIQUE), `verified_email` (UNIQUE), `contact_name`, `title`, `tech_stack`, `status`, `custom_subject`, `custom_pitch`, `created_at`, `updated_at`, `notes`, plus a set of legacy outreach-sequencer columns (`email_1_sent_at`, `followup_1_due_date`, etc.) retained for backward read/write compatibility with historical rows only.

### `lead_touches` (8 columns, mapped by `models.LeadTouch`)

`id`, `lead_id` (FK → `leads.id`, `ON DELETE CASCADE`), `touch_type`, `subject`, `body`, `status`, `sent_at`, `created_at`.

---

## Status Lifecycle

```
QUALIFIED ──► QUEUED ──► SENT ──► REPLIED
    │            │                  │
    ▼            ▼                  ▼
DISQUALIFIED  ARCHIVED           ARCHIVED
    │
    ▼
 QUALIFIED (re-qualify)
```

The full transition graph lives in `config.ALLOWED_TRANSITIONS` and is enforced exclusively by `lead_service.transition_lead_status`. `UNPROCESSED` is never referenced anywhere in this application; any pre-existing legacy rows with that (or any other historical) status are read-only in the UI.

---

## Operational Workflows

### Workflow 1 — Manual Ingestion

1. Navigate to the **📥 Manual Ingestion** tab.
2. Fill in `Company Name` (required), plus any of `Contact Name`, `Website`, `Contact Title`, `Email`, `Tech Stack`, `Notes`.
3. Click **Add Lead**. On success the lead is created with status `QUALIFIED`. On a duplicate `email`/`website`, a 409 Conflict payload is shown.

### Workflow 2 — Cold Triage Desk

1. Navigate to the **❄️ Cold Triage Desk** tab.
2. Select a lead from the dropdown — the selector exclusively lists leads with `status == QUALIFIED`.
3. The subject/body pitch draft is auto-populated via the Jinja2 interpolation helper (contact first name + tech stack). Edit it freely — it remains fully editable at all times.
4. Optionally click **Regenerate Draft** to re-render the default template (discarding manual edits).
5. Click **Queue Lead** to persist the edited draft and transition the lead directly to `QUEUED`.

### Workflow 3 — Master Ledger

1. Navigate to the **📒 Master Ledger** tab.
2. Search/filter across all leads.
3. Select a lead and apply a manual status override — only legally allowed target statuses are offered.

---

## Testing & Verification

Run the deterministic backend verification suite:

```bash
python verify_backend.py
```

This suite runs **exclusively against an isolated temporary SQLite database** created via `tempfile` — it never opens the production `leads.db`. It verifies: deduplication on `email`/`website`, default `QUALIFIED` status, legal/illegal status transitions, rejection of `UNPROCESSED`, first-name extraction, Jinja2 draft rendering + persistence, and the `Lead.touches` cascade relationship.

---

## Project Structure

```
B2B_Substrate/
├── .env.example
├── .gitignore
├── .streamlit/
│   └── config.toml
├── launch.bat
├── memory-bank/
│   ├── projectbrief.md
│   ├── productContext.md
│   ├── activeContext.md
│   ├── systemPatterns.md
│   ├── techContext.md
│   └── progress.md
├── app.py                # Streamlit UI (3 tabs + KPI ribbon)
├── config.py              # DB path + 6-state lifecycle + transition graph
├── database.py            # SQLAlchemy engine/session/init_db
├── models.py              # Lead / LeadTouch Declarative Base models
├── lead_service.py        # Ingestion, dedup, lifecycle, draft service layer
├── templates_engine.py    # Jinja2 draft interpolation engine
├── requirements.txt
├── verify_backend.py      # Deterministic backend verification (temp DB only)
└── leads.db               # Pre-existing SQLite database (never altered)
```

---

## Data Integrity Guarantee

This refactor was performed under a strict **zero-data-loss** constraint. Before and after every change:

- `leads` row count: **15 → 15** (unchanged)
- `lead_touches` row count: **0 → 0** (unchanged)
- `leads` table `CREATE TABLE` SQL: byte-identical
- No `ALTER TABLE`, `DROP TABLE`, or destructive migration was ever executed

`database.init_db()` is intentionally limited to `Base.metadata.create_all(engine, checkfirst=True)`, which SQLAlchemy documents as a strict "create if absent" — a complete no-op for tables that already exist.

---

## License

Proprietary — All rights reserved.

---

*B2B Substrate — Built for Rob Rowan. Phase 4 Backend Refactor.*
