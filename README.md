# 📡 B2B Substrate

**Security-Conscious B2B Lead Triage Engine & Email Sequencer**

B2B Substrate is a local-first, production-grade Streamlit application that ingests scraped partner agency website data, qualifies leads via Google Vertex AI Gemini 3.6 Flash, generates hyper-specific cold email pitches, and manages a complete outreach sequence with automatic business-day follow-up scheduling.

---

## Table of Contents

1. [System Architecture Overview](#system-architecture-overview)
2. [Core Capabilities](#core-capabilities)
3. [Local Installation & Dependency Setup](#local-installation--dependency-setup)
4. [Vertex AI GCP Credential Configuration](#vertex-ai-gcp-credential-configuration)
5. [Porkbun DNS Setup Guide (SPF, DKIM, DMARC)](#porkbun-dns-setup-guide-spf-dkim-dmarc)
6. [Database Schema Reference](#database-schema-reference)
7. [Operational Workflows](#operational-workflows)
8. [Email Templates & Sequence](#email-templates--sequence)
9. [Security & Compliance](#security--compliance)
10. [Testing & Verification](#testing--verification)
11. [Project Structure](#project-structure)
12. [Troubleshooting](#troubleshooting)

---

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Streamlit UI (app.py)                        │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────────┐  │
│  │  Ingestion  │ │     LLM      │ │ Cold Triage  │ │ Follow-Up   │  │
│  │             │ │ Qualification│ │ Desk         │ │ Radar       │  │
│  └─────────────┘ └──────────────┘ └──────────────┘ └─────────────┘  │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    Master Ledger + KPI Ribbon                 │  │
│  └───────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                    Ingestion Pipeline (ingestion.py)                │
│        httpx fetch → sanitize → verify → deduplicate → insert       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                 Deliverability Firewall (verifier.py)               │
│        DNS MX lookup → disposable blocklist → role-based filter     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                    Sanitization Layer (sanitizer.py)                │
│   HTML strip → entity decode → control chars → injection defense    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                    LLM Engine (llm_engine.py)                       │
│        Vertex AI Gemini 3.6 Flash → Pydantic structured output      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                    SMTP Dispatcher (emailer.py)                     │
│        Rolling 24h cap (20/day) → plain-text dispatch → logging     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                    Database Layer (database.py)                     │
│        SQLite (leads.db) — UNIQUE(domain) + UNIQUE(verified_email)  │
└─────────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Ingest** — Raw JSON endpoints, uploaded JSON files, pasted JSON, or manual entry feed lead data into the pipeline.
2. **Sanitize** — All web context passes through the sanitizer to strip HTML, scripts, control characters, and prompt injection signatures.
3. **Verify** — The deliverability firewall performs DNS MX lookups and rejects disposable, role-based, or risky email patterns.
4. **Deduplicate** — Domains and emails already in `leads.db` are silently skipped.
5. **Qualify** — Gemini 3.6 Flash analyzes sanitized agency text and outputs a structured verdict (`QUALIFIED` / `DISQUALIFIED`), reasoning, and a custom 3-sentence pitch.
6. **Triage** — The Cold Triage Desk displays the parsed tech stack side-by-side with the editable pitch. One click approves and queues the email.
7. **Dispatch** — The SMTP dispatcher enforces a strict rolling 24-hour ceiling of 20 emails and logs exact dispatch timestamps.
8. **Sequence** — Follow-up 1 is scheduled at +3 business days; the breakup email at +10 business days.
9. **Radar** — The Follow-Up Radar surfaces any lead whose due date has arrived or passed.
10. **Ledger** — The Master Ledger provides full search, filtering, and manual status override capability.

---

## Core Capabilities

| Capability | Implementation |
|-----------|---------------|
| **LLM Qualification** | Vertex AI Gemini 3.6 Flash with Pydantic-enforced structured JSON output |
| **Cold Email Generation** | Hyper-specific 3-sentence plain-text pitch with exactly two plain-text URLs in the signature |
| **Deliverability Firewall** | DNS MX lookup, disposable domain blocklist, role-based email rejection, risky pattern detection |
| **Zero Double-Sends** | Database-level `UNIQUE` constraints on `domain` and `verified_email` |
| **Prompt Injection Defense** | 24 known injection signatures stripped before LLM consumption |
| **Rolling Send Throttle** | Hard ceiling of 20 emails per rolling 24-hour window (first emails + follow-ups combined) |
| **Business-Day Scheduling** | Follow-up 1 at +3 business days, Breakup at +10 business days (weekends skipped) |
| **11-State Lifecycle** | `UNPROCESSED → QUALIFIED → EMAIL_1_SENT → FOLLOWUP_1_DUE → FOLLOWUP_1_SENT → FOLLOWUP_2_DUE → BREAKUP_SENT` with terminal states `REPLIED`, `MEETING_BOOKED`, `SKIPPED`, `BOUNCED` |
| **Multi-Mode Ingestion** | JSON endpoint URL, JSON file upload, raw JSON paste, and manual entry — flat arrays and nested wrappers handled automatically |
| **Live KPI Ribbon** | Total Leads, Active Outreach, Sent Today (vs. cap), Follow-Ups Due, Meetings Booked |

---

## Local Installation & Dependency Setup

### Prerequisites

- **Python 3.11+**
- **pip** (Python package manager)
- **Git** (optional, for version control)

### Dependencies

The project depends on the following packages (see `requirements.txt`):

| Package | Purpose |
|---------|---------|
| `streamlit` | Web UI framework |
| `pydantic` | Structured output validation |
| `pydantic-ai` | AI agent utilities |
| `httpx` | HTTP ingestion client |
| `google-genai` | Google GenAI SDK (Gemini) |
| `google-cloud-aiplatform` | Vertex AI platform client |
| `google-auth` | GCP authentication |
| `dnspython` | DNS MX record lookups |

### Windows One-Click Launch (Recommended)

On Windows, simply double-click **`launch.bat`** in the project root. The script will:

1. Verify `app.py` is present in the project root.
2. Locate a Python 3.11+ interpreter (falls back to the `py` launcher if `python` is not on `PATH`).
3. Create a `.venv` virtual environment on first run.
4. Install dependencies from `requirements.txt` (re-runs automatically whenever `requirements.txt` changes, tracked via a SHA-256 hash).
5. Warn if port 8501 is already occupied (Streamlit will fall back to 8502/8503).
6. Launch the Streamlit app and **auto-open the default browser** on the first responding port (8501 → 8502 → 8503).

> **Note**: The `.bat` file must stay in the project root alongside `app.py`. Press `Ctrl+C` in the launcher window to stop the server.

### Step 1 — Clone or Create the Project

```bash
git clone <your-repo-url> B2B_Substrate
cd B2B_Substrate
```

### Step 2 — Create a Virtual Environment

```bash
python -m venv .venv
```

**Windows (CMD):**

```cmd
.venv\Scripts\activate
```

**Windows (PowerShell):**

```powershell
.venv\Scripts\Activate.ps1
```

**macOS / Linux:**

```bash
source .venv/bin/activate
```

### Step 3 — Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4 — Configure Environment Variables

Create a `.env` file in the project root (or set system environment variables). A template is provided in `.env.example`:

```env
# --- GCP / Vertex AI ---
GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\your\service-account.json
GCP_PROJECT=b2b-substrate
GCP_LOCATION=us-central1
GEMINI_MODEL_NAME=gemini-2.5-flash

# --- SMTP (Brevo / Resend / any provider) ---
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USERNAME=your-smtp-username
SMTP_PASSWORD=your-smtp-password
SMTP_FROM_EMAIL=you@yourdomain.com
SMTP_FROM_NAME=Rob Rowan
```

> **Note**: The `.env.example` file ships with the SMTP variables only. The GCP variables above are optional — the application resolves GCP credentials automatically (see the next section).

### Step 5 — Launch the Application

**Windows (one-click):**

Double-click **`launch.bat`** in the project root — it handles the virtual environment, dependency installation, and server startup automatically.

**Windows (manual):**

```cmd
.venv\Scripts\activate
streamlit run app.py
```

**macOS / Linux:**

```bash
source .venv/bin/activate
streamlit run app.py
```

The application will be available at **http://localhost:8501**.

---

## Vertex AI GCP Credential Configuration

### Option A — Environment Variable (Recommended)

1. Create a service account in the [Google Cloud Console](https://console.cloud.google.com/):
   - Navigate to **IAM & Admin → Service Accounts**.
   - Click **Create Service Account**.
   - Grant the service account the **Vertex AI User** role.
   - Click **Create Key** and download the JSON key file.

2. Set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable:

   **Windows (CMD):**
   ```cmd
   setx GOOGLE_APPLICATION_CREDENTIALS "C:\path\to\your\service-account.json"
   ```

   **Windows (PowerShell):**
   ```powershell
   $env:GOOGLE_APPLICATION_CREDENTIALS = "C:\path\to\your\service-account.json"
   ```

   **macOS / Linux:**
   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your/service-account.json"
   ```

### Option B — Local File Fallback

Place a `service_account.json` file in the project root. The application automatically detects it when the environment variable is not set.

### Option C — User-Specified Path

The application also checks a user-specified path defined in `config.py` (`USER_GCP_SERVICE_ACCOUNT_PATH`). Update this constant to point to your key file.

### Credential Resolution Order

1. `GOOGLE_APPLICATION_CREDENTIALS` environment variable
2. User-specified path in `config.py`
3. Local `service_account.json` in the project root

### Enable the Vertex AI API

```bash
gcloud services enable aiplatform.googleapis.com --project=rob-vertex-production
```

---

## Porkbun DNS Setup Guide (SPF, DKIM, DMARC)

To protect your sender reputation and maximize deliverability, configure the following DNS records in your Porkbun domain dashboard.

### 1. SPF (Sender Policy Framework)

SPF declares which servers are authorized to send email on behalf of your domain.

| Type | Host | Value | TTL |
|------|------|-------|-----|
| TXT | `@` | `v=spf1 include:spf.brevo.com ~all` | 600 |

> **Note**: Replace `spf.brevo.com` with your provider's SPF include (e.g., `spf.sendgrid.net`, `amazonses.com`). Never have more than one SPF record — merge multiple includes into a single record.

### 2. DKIM (DomainKeys Identified Mail)

DKIM cryptographically signs your emails so receiving servers can verify they were not tampered with.

1. Log in to your **Brevo** (or provider) dashboard.
2. Navigate to **Settings → Senders & IPs → Domains**.
3. Add your domain and follow the DKIM setup instructions.
4. Porkbun will typically require a record like:

| Type | Host | Value | TTL |
|------|------|-------|-----|
| TXT | `brevo._domainkey` | `k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC...` | 600 |

> **Note**: The exact host and value are provided by your email provider. Copy them exactly.

### 3. DMARC (Domain-based Message Authentication, Reporting & Conformance)

DMARC tells receiving servers what to do with emails that fail SPF or DKIM checks.

| Type | Host | Value | TTL |
|------|------|-------|-----|
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:dmarc@yourdomain.com; pct=100` | 600 |

Start with `p=none` to monitor, then tighten to `p=quarantine` after 2–4 weeks of clean reports.

### 4. MX Records (Required for Receiving Replies)

| Type | Host | Priority | Value | TTL |
|------|------|----------|-------|-----|
| MX | `@` | 0 | `mx1.brevo.com` | 600 |
| MX | `@` | 20 | `mx2.brevo.com` | 600 |

> **Note**: The B2B Substrate verifier performs DNS MX lookups on every ingested email domain. Domains without MX records are rejected to protect your sender reputation.

### 5. Custom Tracking Domain (Optional)

For advanced deliverability, configure a custom tracking domain in Brevo and add the corresponding CNAME records in Porkbun.

---

## Database Schema Reference

### `leads` Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Auto-generated lead ID |
| `company_name` | TEXT | NOT NULL | Agency company name |
| `domain` | TEXT | NOT NULL UNIQUE | Agency domain (deduplication key) |
| `verified_email` | TEXT | NOT NULL UNIQUE | Verified contact email (deduplication key) |
| `contact_name` | TEXT | | Contact person name |
| `title` | TEXT | | Contact job title |
| `tech_stack` | TEXT | | Parsed technology stack summary |
| `website_text` | TEXT | | Raw scraped website text |
| `sanitized_text` | TEXT | | Sanitized website text (post-pipeline) |
| `qualification_verdict` | TEXT | | `QUALIFIED` or `DISQUALIFIED` |
| `reasoning` | TEXT | | LLM reasoning for the verdict |
| `custom_pitch` | TEXT | | Generated 3-sentence cold email pitch |
| `status` | TEXT | NOT NULL DEFAULT `UNPROCESSED` | Current lead state |
| `created_at` | TEXT | NOT NULL | ISO-8601 creation timestamp |
| `updated_at` | TEXT | NOT NULL | ISO-8601 last-update timestamp |
| `email_1_sent_at` | TEXT | | ISO-8601 first email dispatch timestamp |
| `followup_1_due_date` | TEXT | | ISO-8601 follow-up 1 due date (+3 business days) |
| `followup_1_sent_at` | TEXT | | ISO-8601 follow-up 1 dispatch timestamp |
| `followup_2_due_date` | TEXT | | ISO-8601 breakup due date (+10 business days) |
| `breakup_sent_at` | TEXT | | ISO-8601 breakup dispatch timestamp |
| `replied_at` | TEXT | | ISO-8601 reply timestamp |
| `skipped_at` | TEXT | | ISO-8601 skip timestamp |
| `bounced_at` | TEXT | | ISO-8601 bounce timestamp |
| `notes` | TEXT | | Free-form notes |

### Indexes

| Index | Columns |
|-------|---------|
| `idx_leads_status` | `status` |
| `idx_leads_followup_1_due` | `followup_1_due_date` |
| `idx_leads_followup_2_due` | `followup_2_due_date` |
| `idx_leads_created_at` | `created_at` |

### Lead State Machine

The lifecycle is an **11-state machine**:

```
UNPROCESSED ──► QUALIFIED ──► EMAIL_1_SENT ──► FOLLOWUP_1_DUE ──► FOLLOWUP_1_SENT
     │              │              │                  │                  │
     │              │              │                  │                  ▼
     │              │              │                  │          FOLLOWUP_2_DUE
     │              │              │                  │                  │
     │              │              │                  │                  ▼
     │              │              │                  │            BREAKUP_SENT
     │              │              │                  │
     │              ▼              ▼                  ▼
     └────────► SKIPPED      REPLIED / BOUNCED   REPLIED / BOUNCED
                              MEETING_BOOKED
```

**Terminal states**: `REPLIED`, `MEETING_BOOKED`, `SKIPPED`, `BOUNCED`.

**Active outreach states** (counted in the "Active Outreach" KPI): `EMAIL_1_SENT`, `FOLLOWUP_1_DUE`, `FOLLOWUP_1_SENT`, `FOLLOWUP_2_DUE`.

---

## Operational Workflows

### Workflow 1 — Ingest New Leads

1. Navigate to the **📥 Ingestion** tab.
2. Choose one of **four** ingestion paths:
   - **Manual Entry**: Fill in company name, domain, email, and optional website text, then click **Add Lead**.
   - **JSON Endpoint URL**: Paste a URL returning a JSON array of lead objects, then click **Fetch & Ingest**.
   - **JSON File Upload**: Drag-and-drop a `.json` file dumped from DevTools or a scraping tool, then click **Ingest Uploaded File**.
   - **Raw JSON Paste**: Paste raw JSON text directly, then click **Ingest Pasted JSON**.
3. The pipeline automatically:
   - Handles both flat JSON arrays (`[{...}]`) and nested wrappers (`{"data": [...]}`, `{"partners": [...]}`, `{"leads": [...]}`, `{"results": [...]}`, `{"items": [...]}`).
   - Sanitizes all web context.
   - Verifies email deliverability (DNS MX + pattern checks).
   - Silently skips duplicate domains/emails.
4. Review the ingestion summary for inserted/skipped/failed counts.

> **Pro Tip**: The Ingestion tab includes a built-in **"Step-by-Step Guide: How to Extract Partner JSONs"** with platform-specific playbooks for Make.com Partners, Zapier Certified Experts, Odoo Partner Network, AWS Partner Network, and Salesforce/HubSpot directories. It walks you through the DevTools "Fetch/XHR" trick to capture raw JSON endpoints.

### Workflow 2 — Qualify Leads with Gemini

1. Navigate to the **🤖 LLM Qualification** tab.
2. Select an unprocessed lead from the dropdown.
3. Click **Run Qualification**.
4. Gemini 3.6 Flash analyzes the sanitized website text and returns:
   - `QUALIFIED` or `DISQUALIFIED` verdict.
   - Reasoning for the decision.
   - A hyper-specific 3-sentence custom pitch.
5. Qualified leads move to the Cold Triage Desk. Disqualified leads are auto-skipped.

### Workflow 3 — Triage & Approve

1. Navigate to the **❄️ Cold Triage Desk** tab.
2. Review the parsed tech stack, verdict, reasoning, and generated pitch.
3. Edit the pitch if needed (plain text only).
4. Click **Approve & Queue** to dispatch the email immediately (subject to the daily cap) and schedule follow-up 1 at +3 business days.
5. Click **Skip** to move the lead to the `SKIPPED` state.

### Workflow 4 — Follow-Up Radar

1. Navigate to the **📡 Follow-Up Radar** tab.
2. Leads whose follow-up due date has arrived or passed are surfaced automatically.
3. Click **Send Follow-Up 1** to dispatch the 3-day bump and schedule the breakup at +10 business days.
4. Click **Send Breakup Email** to dispatch the 10-day breakup.
5. Use **Mark Replied**, **Meeting Booked**, or **Mark Bounced** to update the lead state.

### Workflow 5 — Master Ledger

1. Navigate to the **📒 Master Ledger** tab.
2. Search by company, domain, email, or contact name.
3. Filter by lead status (all 11 states).
4. Select a lead and apply a manual status override (`REPLIED`, `MEETING_BOOKED`, `BOUNCED`, etc.).

### KPI Ribbon & Sidebar

The top of the app renders a **5-card KPI ribbon** with live database metrics:

| KPI | Description |
|-----|-------------|
| **Total Leads** | All leads in the pipeline |
| **Active Outreach** | Leads currently in the email sequence |
| **Sent Today** | `sent / 20` with remaining count in the daily cap |
| **Follow-Ups Due** | Due today or overdue |
| **Meetings Booked** | Leads in the `MEETING_BOOKED` state |

The **sidebar** shows live **Credential Status** (GCP), **SMTP Status**, and a **Daily Send Cap** progress bar.

---

## Email Templates & Sequence

The outreach sequence consists of three plain-text emails, each signed with exactly two plain-text URLs (GitHub + LinkedIn) and no HTML, markdown, or tracking pixels.

### 1. First Cold Email (`EMAIL_1`)

- **Subject**: `quick dev question`
- **Body**: The generated 3-sentence custom pitch followed by the signature block.
- **Requires**: Lead in `QUALIFIED` state with a custom pitch.
- **On success**: Lead → `EMAIL_1_SENT`, follow-up 1 scheduled at +3 business days.

### 2. Follow-Up 1 (`FOLLOWUP_1`)

- **Subject**: `Re: quick dev question`
- **Body**: A templated 3-day bump referencing the previous note and offering a 15-minute call.
- **Requires**: Lead in `FOLLOWUP_1_DUE` state.
- **On success**: Lead → `FOLLOWUP_1_SENT`, breakup scheduled at +10 business days.

### 3. Breakup Email (`BREAKUP`)

- **Subject**: `Closing the loop - {company_name}`
- **Body**: A templated 10-day breakup closing the thread while leaving the door open.
- **Requires**: Lead in `FOLLOWUP_2_DUE` state.
- **On success**: Lead → `BREAKUP_SENT`.

### Rolling Send Throttle

All three email types share a single **rolling 24-hour ceiling of 20 emails**. The count is computed from the `email_1_sent_at`, `followup_1_sent_at`, and `breakup_sent_at` timestamps. When the cap is reached, dispatch attempts are deferred (the lead state still advances so the sequence is not blocked).

---

## Security & Compliance

### Credential Protection

- **GCP service account keys are never committed** to version control (see `.gitignore`).
- SMTP credentials are loaded from environment variables, never hard-coded.
- The `.gitignore` excludes all JSON files by default to prevent accidental credential leakage.

### Prompt Injection Defense

- All raw scraped web content passes through the sanitizer before reaching Gemini.
- 24 known injection signatures are stripped from the text.
- The sanitizer removes HTML, scripts, styles, comments, and control characters.
- Generated email bodies are re-sanitized to strip markdown, tracking-pixel markup, and HTML.

### Deliverability Protection

- DNS MX lookup confirms the recipient domain can receive email.
- 20 disposable email domains are blocked.
- Role-based addresses (`info@`, `sales@`, `support@`) are rejected.
- Plus-addresses and numeric local parts are flagged as risky.
- A strict rolling 24-hour send ceiling of 20 emails protects sender reputation.

### Zero Double-Send Guarantee

- Database-level `UNIQUE` constraints on `domain` and `verified_email`.
- Application-level deduplication checks before every insert.
- The state machine prevents re-sending to leads already in the sequence.

---

## Testing & Verification

The project ships with **`verify_phase2.py`**, a deterministic integration verification script that runs assertions against the Phase 2 modules and exits with a non-zero code if any check fails.

### Running the Verification Suite

```bash
python verify_phase2.py
```

### What It Verifies

| # | Check | Description |
|---|-------|-------------|
| 1 | **Database: state machine + rolling window count** | Inserts a lead, transitions it through `QUALIFIED → EMAIL_1_SENT`, asserts the follow-up due date is set and the rolling 24-hour send count increments. |
| 2 | **Sanitizer: HTML stripping + entity decode** | Asserts `<script>` blocks and HTML entities are removed. |
| 3 | **Verifier: disposable + role-based rejection** | Asserts disposable domains are blocked and role-based addresses are rejected. |
| 4 | **Email builders: signature URLs + templates** | Asserts the signature contains exactly the two plain-text URLs and the follow-up/breakup templates build correctly. |
| 5 | **LLM Engine: credentials + Pydantic schema** | Asserts GCP credentials resolve and the `LeadEvaluation` schema requires `qualification_verdict`, `reasoning`, and `custom_pitch`. |
| 6 | **Meeting Booked: state + KPI count** | Asserts the `MEETING_BOOKED` state exists in `LEAD_STATES` and the KPI count helper works. |
| 7 | **Ingestion: dedup + verification filtering** | Asserts duplicate domains are skipped and role-based emails are filtered out. |

On success, the script prints `=== ALL PHASE 2 VERIFICATIONS PASSED ===`.

---

## Project Structure

```
B2B_Substrate/
├── .env.example                 # Environment variable template (SMTP)
├── .gitignore                   # Git ignore rules (credentials, DB, caches)
├── .streamlit/
│   └── config.toml              # Streamlit theme + server config
├── launch.bat                   # Windows one-click launcher (venv + deps + run)
├── memory-bank/                 # Project documentation (git-ignored)
│   ├── projectbrief.md
│   ├── productContext.md
│   ├── activeContext.md
│   ├── systemPatterns.md
│   ├── techContext.md
│   └── progress.md
├── app.py                       # Streamlit UI layer (5 tabs + KPI ribbon + sidebar)
├── config.py                    # Configuration + credential resolution
├── database.py                  # SQLite persistence + state machine
├── emailer.py                   # SMTP dispatch + rolling 24h throttle + templates
├── ingestion.py                 # httpx ingestion + sanitization + dedup
├── llm_engine.py                # Vertex AI Gemini 3.6 Flash + Pydantic output
├── requirements.txt             # Python dependencies
├── sanitizer.py                 # Input sanitization pipeline
├── verifier.py                  # DNS MX + email deliverability firewall
├── verify_phase2.py             # Phase 2 integration verification script
└── leads.db                     # SQLite database (created at runtime)
```

---

## Troubleshooting

### GCP Credentials Not Found

```
No GCP service account credentials found.
```

**Fix**: Set `GOOGLE_APPLICATION_CREDENTIALS` to your service account JSON path, or place `service_account.json` in the project root.

### Vertex AI API Not Enabled

```
PERMISSION_DENIED: Vertex AI API has not been used in project ...
```

**Fix**:
```bash
gcloud services enable aiplatform.googleapis.com --project=rob-vertex-production
```

### SMTP Relay Not Configured

```
SMTP relay is not configured.
```

**Fix**: Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM_EMAIL` environment variables.

### Daily Send Cap Reached

```
Daily send cap reached.
```

**Fix**: The rolling 24-hour window has been exhausted. Wait for the window to roll over or adjust `DAILY_SEND_CAP` in `config.py`.

### Email Verification Failing

```
Domain has no MX records and cannot receive email.
```

**Fix**: The recipient domain does not have MX records configured. Verify the email address is correct, or use `EmailVerifier(require_mx=False)` for testing.

### Port 8501 Already in Use

```
[NOTE] A server is already running on port 8501.
```

**Fix**: The launcher warns you and Streamlit automatically falls back to port 8502 or 8503. The launcher auto-opens the browser on the first responding port.

---

## License

Proprietary — All rights reserved.

---

*B2B Substrate — Built for Rob Rowan. Phase 2 Production Release.*