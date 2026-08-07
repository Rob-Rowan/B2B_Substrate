"""Streamlit UI layer for B2B Substrate.

This module provides the Streamlit front-end for the B2B lead triage
engine.  It renders a dark slate/charcoal developer theme, a top KPI
ribbon, and five functional tabs:

1. Ingestion - add leads from JSON endpoints, file uploads, paste, or
   manual entry.
2. LLM Qualification - run Gemini 2.5 Flash on unprocessed leads.
3. Cold Triage Desk - review, edit, and approve or skip qualified leads.
4. Follow-Up Radar - surface leads due for follow-up.
5. Master Ledger - searchable data grid with manual status overrides.

The application wires the full pipeline: lead ingestion, LLM
qualification, email dispatch, and follow-up scheduling.
"""

from __future__ import annotations

import json
from typing import Any, Final

import streamlit as st

from config import DAILY_SEND_CAP, LEAD_STATES, load_config
from database import Database
from email_enricher import (
    generate_email_candidates,
    is_placeholder_email,
)
from emailer import Emailer
from ingestion import IngestionPipeline, IngestionSummary
from llm_engine import LLMEngine, LeadEvaluation
from verifier import verify_mailbox

# ---------------------------------------------------------------------------
# Theme constants
# ---------------------------------------------------------------------------

_BG_COLOR: Final[str] = "#1E222A"
_CARD_COLOR: Final[str] = "#2B303C"
_TEXT_COLOR: Final[str] = "#E2E8F0"
_ACCENT_COLOR: Final[str] = "#4F8CC9"
_SUCCESS_COLOR: Final[str] = "#2E7D32"
_WARNING_COLOR: Final[str] = "#B26A00"
_DANGER_COLOR: Final[str] = "#C62828"

_CUSTOM_CSS: Final[str] = f"""
<style>
    .stApp {{
        background-color: {_BG_COLOR};
        color: {_TEXT_COLOR};
    }}

    .block-container {{
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }}

    h1, h2, h3, h4, h5, h6 {{
        color: {_TEXT_COLOR} !important;
    }}

    .kpi-card {{
        background-color: {_CARD_COLOR};
        border-radius: 8px;
        padding: 1rem 1.25rem;
        margin-bottom: 0.5rem;
        border-left: 4px solid {_ACCENT_COLOR};
    }}

    .kpi-card .kpi-label {{
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #94A3B8;
        margin-bottom: 0.25rem;
    }}

    .kpi-card .kpi-value {{
        font-size: 1.75rem;
        font-weight: 700;
        color: {_TEXT_COLOR};
    }}

    .kpi-card .kpi-sub {{
        font-size: 0.75rem;
        color: #94A3B8;
        margin-top: 0.25rem;
    }}

    .lead-card {{
        background-color: {_CARD_COLOR};
        border-radius: 8px;
        padding: 1.25rem;
        margin-bottom: 1rem;
        border: 1px solid #3A4150;
    }}

    .lead-card .lead-title {{
        font-size: 1.1rem;
        font-weight: 600;
        color: {_TEXT_COLOR};
        margin-bottom: 0.5rem;
    }}

    .lead-card .lead-meta {{
        font-size: 0.8rem;
        color: #94A3B8;
        margin-bottom: 0.5rem;
    }}

    .lead-card .lead-body {{
        font-size: 0.9rem;
        color: {_TEXT_COLOR};
        line-height: 1.5;
    }}

    .status-badge {{
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 4px;
        font-size: 0.7rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.03em;
    }}

    .status-UNPROCESSED {{ background-color: #374151; color: #E2E8F0; }}
    .status-QUALIFIED {{ background-color: #1E3A5F; color: #93C5FD; }}
    .status-EMAIL_1_SENT {{ background-color: #1B4332; color: #95D5B2; }}
    .status-FOLLOWUP_1_DUE {{ background-color: #5C3A00; color: #FCD34D; }}
    .status-FOLLOWUP_1_SENT {{ background-color: #1B4332; color: #95D5B2; }}
    .status-FOLLOWUP_2_DUE {{ background-color: #5C3A00; color: #FCD34D; }}
    .status-BREAKUP_SENT {{ background-color: #3B2F2F; color: #E7C6C6; }}
    .status-REPLIED {{ background-color: #14532D; color: #86EFAC; }}
    .status-MEETING_BOOKED {{ background-color: #0F766E; color: #99F6E4; }}
    .status-SKIPPED {{ background-color: #374151; color: #9CA3AF; }}
    .status-BOUNCED {{ background-color: #7F1D1D; color: #FCA5A5; }}

    .missing-badge {{
        display: inline-block;
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
        font-size: 0.7rem;
        font-weight: 600;
        background-color: #7F1D1D;
        color: #FCA5A5;
        margin-left: 0.5rem;
        vertical-align: middle;
    }}

    .mailbox-warning-badge {{
        display: inline-block;
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
        font-size: 0.7rem;
        font-weight: 600;
        background-color: #5C3A00;
        color: #FCD34D;
        margin-left: 0.5rem;
        vertical-align: middle;
    }}

    .stButton > button {{
        background-color: {_ACCENT_COLOR};
        color: #FFFFFF;
        border: none;
        border-radius: 6px;
        font-weight: 600;
    }}

    .stButton > button:hover {{
        background-color: #3D7AB8;
        color: #FFFFFF;
    }}

    .stTextInput input, .stTextArea textarea,
    .stSelectbox div[data-baseweb="select"] > div {{
        background-color: {_CARD_COLOR};
        color: {_TEXT_COLOR};
        border-color: #3A4150;
    }}

    .stDataFrame {{
        background-color: {_CARD_COLOR};
    }}

    .stTabs [data-baseweb="tab-list"] {{
        gap: 0.5rem;
    }}

    .stTabs [data-baseweb="tab"] {{
        background-color: {_CARD_COLOR};
        border-radius: 6px 6px 0 0;
        padding: 0.5rem 1.25rem;
        color: {_TEXT_COLOR};
    }}

    .stTabs [aria-selected="true"] {{
        background-color: {_ACCENT_COLOR};
        color: #FFFFFF;
    }}

    .stSidebar {{
        background-color: {_CARD_COLOR};
    }}

    .stSidebar .stMarkdown p {{
        color: {_TEXT_COLOR};
    }}

    .stMetric {{
        background-color: {_CARD_COLOR};
        border-radius: 8px;
        padding: 1rem;
    }}

    .stMetric label {{
        color: #94A3B8 !important;
    }}

    .stMetric [data-testid="stMetricValue"] {{
        color: {_TEXT_COLOR};
    }}
</style>
"""

_INGESTION_GUIDE_MARKDOWN: Final[str] = """
### 1. Universal Extraction Protocol (The DevTools Trick)

**Step 1 — Open the target partner directory**

Open Chrome or Firefox and navigate to the partner directory you want
to scrape (e.g., Make.com Partners, Zapier Experts, Odoo Partners).

**Step 2 — Open Developer Tools**

Press `F12` to open Developer Tools and switch to the **Network** tab.
Click the **Fetch/XHR** filter so only API requests are shown.

**Step 3 — Trigger a request**

Interact with the directory page — scroll down, switch pages, or select
a region filter — to fire new network requests.

**Step 4 — Inspect the response**

Click through the firing requests and check their **Response** tab for
raw JSON arrays containing company names, web domains, and partner
tiers.

**Step 5 — Copy the request**

Right-click the request name → **Copy** → **Copy URL** to paste into
the **JSON Endpoint URL** field below. Alternatively, copy the raw JSON
text directly and paste it into the **Raw JSON Paste** box.

---

### 2. Platform-Specific Playbooks

#### Make.com Partners (`make.com/en/partners`)

1. Open `make.com/en/partners` in Chrome or Firefox.
2. Press `F12` and open the **Network** tab.
3. Click the **Fetch/XHR** filter.
4. Scroll the partner directory to trigger card loading.
5. Look for endpoints returning partner cards (company name, domain,
   tier).
6. Right-click the request → **Copy** → **Copy URL**.
7. **Pro Tip:** Edit the `limit=20` parameter in the copied URL to
   `limit=100` before fetching to pull more partners per request.

#### Zapier Certified Experts (`zapier.com/experts`)

1. Open `zapier.com/experts` in Chrome or Firefox.
2. Press `F12` and open the **Network** tab.
3. Click the **Fetch/XHR** filter.
4. Filter the request list for `experts` or GraphQL queries.
5. Click a matching request and inspect its **Response** tab for the
   expert JSON payload.
6. Copy the response URL or the raw JSON text.

#### Odoo Partner Network (`odoo.com/partners`)

1. Open `odoo.com/partners` in Chrome or Firefox.
2. Press `F12` and open the **Network** tab.
3. Click the **Fetch/XHR** filter.
4. Interact with the partner search (type a query or change a filter).
5. Look for `/web/dataset/call_kw` requests or partner search payloads.
6. Copy the JSON payload from the **Payload** or **Response** tab.

#### AWS Partner Network / APN (`partners.amazonaws.com`)

1. Open `partners.amazonaws.com` in Chrome or Firefox.
2. Press `F12` and open the **Network** tab.
3. Click the **Fetch/XHR** filter.
4. Use the catalog search to trigger partner queries.
5. Inspect the firing requests for a JSON response array containing
   competencies and partner domains.
6. Copy the request URL or raw JSON text.

#### Salesforce & HubSpot Directories

1. Open the **Salesforce AppExchange** or the **HubSpot Solutions
   Directory** in Chrome or Firefox.
2. Press `F12` and open the **Network** tab.
3. Click the **Fetch/XHR** filter.
4. Search for a partner or solution to trigger partner search
   endpoints.
5. Inspect the responses for JSON payloads containing partner names,
   domains, and tiers.
6. Copy the JSON payload or request URL.
"""

# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------


def _get_database() -> Database:
    """Return a cached database instance from Streamlit session state.

    Returns:
        Database: The shared database connection for the session.
    """
    if "database" not in st.session_state:
        config = load_config()
        st.session_state["database"] = Database(config.database_path)
    return st.session_state["database"]


def _get_emailer(db: Database) -> Emailer:
    """Return a cached emailer instance from Streamlit session state.

    Args:
        db: The active database connection.

    Returns:
        Emailer: The shared email dispatcher for the session.
    """
    if "emailer" not in st.session_state:
        config = load_config()
        st.session_state["emailer"] = Emailer(
            db, smtp=config.smtp, daily_send_cap=config.daily_send_cap
        )
    return st.session_state["emailer"]


def _get_llm_engine() -> LLMEngine | None:
    """Return a cached LLM engine instance, or ``None`` on failure.

    Returns:
        LLMEngine | None: The shared LLM engine, or ``None`` when GCP
            credentials are unavailable.
    """
    if "llm_engine" not in st.session_state:
        try:
            st.session_state["llm_engine"] = LLMEngine()
        except RuntimeError:
            st.session_state["llm_engine"] = None
    return st.session_state["llm_engine"]


def _get_ingestion_pipeline(db: Database) -> IngestionPipeline:
    """Return a cached ingestion pipeline instance.

    Args:
        db: The active database connection.

    Returns:
        IngestionPipeline: The shared ingestion pipeline for the session.
    """
    if "ingestion_pipeline" not in st.session_state:
        st.session_state["ingestion_pipeline"] = IngestionPipeline(db)
    return st.session_state["ingestion_pipeline"]


# ---------------------------------------------------------------------------
# UI rendering helpers
# ---------------------------------------------------------------------------


def render_kpi_ribbon(db: Database) -> None:
    """Render the top KPI ribbon with live database metrics.

    Args:
        db: The active database connection.
    """
    total_leads = db.count_leads()
    active_outreach = db.get_active_outreach_count()
    sent_today = db.get_sent_today_count()
    followups_due = len(db.get_followups_due_today())
    meetings_booked = db.get_meetings_booked_count()

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-label">Total Leads</div>
                <div class="kpi-value">{total_leads}</div>
                <div class="kpi-sub">All leads in pipeline</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-label">Active Outreach</div>
                <div class="kpi-value">{active_outreach}</div>
                <div class="kpi-sub">In email sequence</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col3:
        remaining = max(DAILY_SEND_CAP - sent_today, 0)
        st.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-label">Sent Today</div>
                <div class="kpi-value">{sent_today} / {DAILY_SEND_CAP}</div>
                <div class="kpi-sub">{remaining} remaining in daily cap</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col4:
        st.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-label">Follow-Ups Due</div>
                <div class="kpi-value">{followups_due}</div>
                <div class="kpi-sub">Due today or overdue</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col5:
        st.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-label">Meetings Booked</div>
                <div class="kpi-value">{meetings_booked}</div>
                <div class="kpi-sub">Replied leads</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_status_badge(status: str) -> str:
    """Return an HTML status badge for a lead state.

    Args:
        status: The lead's current state string.

    Returns:
        str: An HTML span with the status badge styling.
    """
    safe_status = status.replace(" ", "_")
    return (
        f'<span class="status-badge status-{safe_status}">{status}</span>'
    )


def render_lead_card(lead: dict[str, Any]) -> None:
    """Render a single lead as a styled card.

    Displays the company name, status badge, contact metadata, and
    qualification details.  When optional fields (``domain``,
    ``verified_email``, ``contact_name``) are missing or empty, a
    warning badge is shown in the card header.  When the LLM produced
    ``search_helpers``, they are displayed as a read-only block.

    Args:
        lead: The lead dictionary to display.
    """
    company = lead.get("company_name", "Unknown Company")
    domain = lead.get("domain") or ""
    email = lead.get("verified_email") or ""
    contact = lead.get("contact_name") or "Unknown Contact"
    title = lead.get("title") or ""
    status = lead.get("status", "UNPROCESSED")
    verdict = lead.get("qualification_verdict") or "N/A"
    reasoning = lead.get("reasoning") or "No reasoning provided."
    pitch = lead.get("custom_pitch") or "No pitch generated yet."
    tech_stack = lead.get("tech_stack") or "Not parsed."
    search_helpers = lead.get("search_helpers") or ""

    # Build missing-field warning badges.
    warning_badges = []
    if not domain:
        warning_badges.append(
            '<span class="missing-badge">Missing Domain</span>'
        )
    if not email:
        warning_badges.append(
            '<span class="missing-badge">Missing Email</span>'
        )
    if not lead.get("contact_name"):
        warning_badges.append(
            '<span class="missing-badge">Missing Contact</span>'
        )

    # Build mailbox verification warning badge.  When the mailbox status
    # is RISKY_CATCHALL, UNKNOWN_UNVERIFIED, or UNVERIFIED_TIMEOUT, a yellow
    # warning badge is shown: "Unverified / Guess - Manual Send Only".
    mailbox_status = lead.get("mailbox_status") or ""
    if mailbox_status in ("RISKY_CATCHALL", "UNKNOWN_UNVERIFIED", "UNVERIFIED_TIMEOUT"):
        warning_badges.append(
            '<span class="mailbox-warning-badge">'
            "Unverified / Guess - Manual Send Only</span>"
        )
    warnings_html = " ".join(warning_badges)

    # Build the metadata line, omitting empty values.
    meta_parts = [f"<b>{contact}</b>"]
    if title:
        meta_parts.append(title)
    if domain:
        meta_parts.append(domain)
    if email:
        meta_parts.append(email)
    if not domain and not email:
        meta_parts.append("<i>No domain or email on record</i>")
    meta_html = " &middot; ".join(meta_parts)

    # Build the body HTML, including search_helpers when present.
    body_parts = [
        f"<b>Verdict:</b> {verdict}<br>",
        f"<b>Reasoning:</b> {reasoning}<br>",
        f"<b>Tech Stack:</b> {tech_stack}<br>",
        f"<b>Pitch:</b><br>",
        f'<pre style="white-space: pre-wrap; font-family: inherit; '
        f'color: {_TEXT_COLOR}; margin-top: 0.5rem;">{pitch}</pre>',
    ]
    if search_helpers:
        body_parts.append(
            f"<b>Search Helpers:</b><br>"
            f'<pre style="white-space: pre-wrap; font-family: inherit; '
            f'color: {_TEXT_COLOR}; margin-top: 0.5rem;">{search_helpers}</pre>'
        )
    body_html = "".join(body_parts)

    st.markdown(
        f"""
        <div class="lead-card">
            <div class="lead-title">
                {company} {render_status_badge(status)} {warnings_html}
            </div>
            <div class="lead-meta">{meta_html}</div>
            <div class="lead-body">
                {body_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------


def _render_ingestion_summary(summary: IngestionSummary) -> None:
    """Render the outcome of a bulk ingestion run.

    Args:
        summary: The aggregated ingestion summary to display.
    """
    st.success(
        f"Ingestion complete: {summary.inserted_count} inserted, "
        f"{summary.skipped_count} skipped, {summary.failed_count} failed."
    )
    if summary.results:
        with st.expander("Ingestion Details"):
            for result in summary.results:
                if result.inserted:
                    st.success(f"{result.company_name}: {result.reason}")
                else:
                    st.warning(f"{result.company_name}: {result.reason}")


def render_ingestion(db: Database) -> None:
    """Render the lead ingestion panel.

    Provides three bulk ingestion modes — a live JSON endpoint URL, an
    uploaded JSON file, and a raw JSON paste box — plus a manual lead
    entry form.  Only ``company_name`` is required for manual entry;
    ``domain`` and ``email`` are optional and may be left blank for
    partial ingestion.  All web context is sanitized and verified before
    storage.

    Args:
        db: The active database connection.
    """
    st.subheader("Lead Ingestion")
    st.caption(
        "Add new leads manually or ingest from a JSON endpoint URL, "
        "uploaded file, or pasted JSON. All web context is sanitized "
        "and verified before storage."
    )

    if "last_ingestion_summary" in st.session_state:
        _render_ingestion_summary(st.session_state["last_ingestion_summary"])
        st.divider()

    with st.expander("Manual Lead Entry", expanded=False):
        with st.form("manual_lead_form"):
            col1, col2 = st.columns(2)
            with col1:
                company_name = st.text_input("Company Name *")
                domain = st.text_input(
                    "Domain (optional)", placeholder="example.com"
                )
                email = st.text_input(
                    "Email (optional)", placeholder="contact@example.com"
                )
            with col2:
                contact_name = st.text_input("Contact Name")
                title = st.text_input("Title")
                notes = st.text_input("Notes")

            website_text = st.text_area(
                "Website Text (raw scraped content)",
                height=120,
                placeholder="Paste raw scraped website text here...",
            )

            submitted = st.form_submit_button("Add Lead", type="primary")

        if submitted:
            if not company_name:
                st.error("Company Name is required.")
            else:
                pipeline = _get_ingestion_pipeline(db)
                result = pipeline.ingest_single_lead(
                    {
                        "company_name": company_name,
                        "domain": domain or None,
                        "verified_email": email or None,
                        "contact_name": contact_name or None,
                        "title": title or None,
                        "website_text": website_text,
                        "notes": notes or None,
                    }
                )
                if result.inserted:
                    st.success(result.reason)
                    st.rerun()
                else:
                    st.warning(result.reason)

    with st.expander(
        "📖 Step-by-Step Guide: How to Extract Partner JSONs", expanded=True
    ):
        st.markdown(_INGESTION_GUIDE_MARKDOWN)

    st.divider()
    st.markdown("### Bulk Ingestion Modes")
    st.caption(
        "Choose one of three bulletproof ingestion modes. Flat JSON "
        "arrays and nested wrappers (e.g. `{\"data\": [...]}` or "
        "`{\"partners\": [...]}`) are handled automatically."
    )

    mode = st.radio(
        "Ingestion mode",
        options=[
            "1. JSON Endpoint URL",
            "2. JSON File Upload",
            "3. Raw JSON Paste",
        ],
        horizontal=True,
    )

    if mode == "1. JSON Endpoint URL":
        json_url = st.text_input(
            "JSON Endpoint URL",
            placeholder="https://example.com/leads.json",
        )
        if st.button("Fetch & Ingest", type="primary"):
            if not json_url:
                st.error("Please provide a JSON endpoint URL.")
            else:
                pipeline = _get_ingestion_pipeline(db)
                try:
                    summary = pipeline.ingest_json_url(json_url)
                    st.session_state["last_ingestion_summary"] = summary
                    st.rerun()
                except Exception as exc:
                    st.error(f"Ingestion failed: {exc}")

    elif mode == "2. JSON File Upload":
        uploaded_file = st.file_uploader(
            "Upload a JSON file",
            type=["json"],
            help=(
                "Drag-and-drop a .json file dumped from DevTools or a "
                "scraping tool."
            ),
        )
        if uploaded_file is not None:
            if st.button("Ingest Uploaded File", type="primary"):
                pipeline = _get_ingestion_pipeline(db)
                try:
                    summary = pipeline.ingest_json_bytes(
                        uploaded_file.getvalue()
                    )
                    st.session_state["last_ingestion_summary"] = summary
                    st.rerun()
                except Exception as exc:
                    st.error(f"Ingestion failed: {exc}")

    else:
        raw_json = st.text_area(
            "Raw JSON Paste",
            height=220,
            placeholder=(
                '[{"company_name": "Acme", "domain": "acme.com", '
                '"email": "dev@acme.com"}]'
            ),
        )
        if st.button("Ingest Pasted JSON", type="primary"):
            if not raw_json.strip():
                st.error("Please paste raw JSON to ingest.")
            else:
                pipeline = _get_ingestion_pipeline(db)
                try:
                    summary = pipeline.ingest_json_text(raw_json)
                    st.session_state["last_ingestion_summary"] = summary
                    st.rerun()
                except Exception as exc:
                    st.error(f"Ingestion failed: {exc}")


def render_llm_qualification(db: Database) -> None:
    """Render the LLM qualification panel.

    Runs the Gemini qualification engine on unprocessed leads and
    stores the structured verdict, reasoning, custom pitch, and
    search helpers back into the database.  Leads missing a domain or
    verified email are passed as ``None`` so the LLM can generate
    targeted search strings.

    Args:
        db: The active database connection.
    """
    st.subheader("LLM Qualification")
    st.caption(
        "Run Gemini 3.6 Flash on unprocessed leads to generate "
        "qualification verdicts and custom cold email pitches."
    )

    engine = _get_llm_engine()
    if engine is None:
        st.warning(
            "GCP credentials not found. Set GOOGLE_APPLICATION_CREDENTIALS "
            "or place a service account JSON key file in the project root."
        )
        return

    unprocessed = db.get_unprocessed_leads()
    if not unprocessed:
        st.info("No unprocessed leads awaiting qualification.")
        return

    lead_options = {
        f"#{lead['id']} - {lead['company_name']} "
        f"({lead.get('domain') or 'no domain'})": int(lead["id"])
        for lead in unprocessed
    }

    selected_label = st.selectbox(
        "Select lead to qualify",
        options=list(lead_options.keys()),
    )
    selected_id = lead_options[selected_label]

    lead = db.get_lead(selected_id)
    if lead is None:
        st.error("Selected lead no longer exists.")
        return

    st.caption(
        f"Sanitized text preview: "
        f"{(lead.get('sanitized_text') or '')[:200]}..."
    )

    if st.button("Run Qualification", type="primary"):
        with st.spinner("Running Gemini 3.6 Flash..."):
            try:
                evaluation: LeadEvaluation = engine.evaluate_lead(
                    company_name=str(lead["company_name"]),
                    website_text=str(lead.get("website_text") or ""),
                    domain=lead.get("domain") or None,
                    verified_email=lead.get("verified_email") or None,
                    contact_name=lead.get("contact_name") or None,
                )
            except Exception as exc:
                st.error(f"Qualification failed: {exc}")
                return

        # Enrich the verified_email when it is missing or a placeholder.
        enriched_email = lead.get("verified_email") or None
        email_candidates_json = None
        if (
            lead.get("domain")
            and is_placeholder_email(enriched_email)
            and lead.get("contact_name")
        ):
            candidates = generate_email_candidates(
                str(lead["contact_name"]), str(lead["domain"])
            )
            if candidates:
                enriched_email = candidates[0]
                email_candidates_json = json.dumps(candidates)

        # Run deep SMTP mailbox verification on the enriched email so the
        # mailbox_status is persisted for the triage desk guardrails.
        mailbox_status = None
        if enriched_email:
            mailbox_result = verify_mailbox(enriched_email)
            mailbox_status = mailbox_result["status"]

        db.update_lead(
            selected_id,
            qualification_verdict=evaluation.qualification_verdict,
            reasoning=evaluation.reasoning,
            custom_pitch=evaluation.custom_pitch,
            search_helpers=evaluation.search_helpers,
            verified_email=enriched_email,
            email_candidates=email_candidates_json,
            mailbox_status=mailbox_status,
        )

        if evaluation.qualification_verdict == "QUALIFIED":
            db.mark_qualified(selected_id)
            st.success(
                f"{lead['company_name']} qualified. Pitch generated."
            )
        else:
            db.mark_skipped(selected_id)
            st.warning(
                f"{lead['company_name']} disqualified. Lead skipped."
            )

        st.rerun()


def render_triage_desk(db: Database) -> None:
    """Render the Cold Triage Desk tab.

    Displays qualified leads awaiting approval.  For each lead, the
    user can manually edit ``domain``, ``verified_email``, and
    ``contact_name`` directly from the lead card, then Approve & Queue
    or Skip.  Missing-field warning badges are shown on each card.

    Args:
        db: The active database connection.
    """
    st.subheader("Cold Triage Desk")
    st.caption(
        "Review qualified leads, edit contact info and the generated "
        "pitch, and approve for the outreach queue or skip."
    )

    qualified_leads = db.get_qualified_leads()

    if not qualified_leads:
        st.info(
            "No qualified leads awaiting triage. Add leads to get started."
        )
        return

    for lead in qualified_leads:
        lead_id = int(lead["id"])
        render_lead_card(lead)

        with st.expander("Edit Pitch & Actions", expanded=False):
            st.markdown("#### Contact Info (edit before sending)")

            col_info1, col_info2 = st.columns(2)
            with col_info1:
                edited_domain = st.text_input(
                    "Domain",
                    value=lead.get("domain") or "",
                    placeholder="example.com",
                    key=f"domain_{lead_id}",
                )
                edited_email = st.text_input(
                    "Verified Email",
                    value=lead.get("verified_email") or "",
                    placeholder="contact@example.com",
                    key=f"email_{lead_id}",
                )
            with col_info2:
                edited_contact = st.text_input(
                    "Contact Name",
                    value=lead.get("contact_name") or "",
                    placeholder="Jane Doe",
                    key=f"contact_{lead_id}",
                )

            # Display generated email alternatives as clickable suggestions.
            stored_candidates = lead.get("email_candidates") or ""
            if stored_candidates:
                try:
                    candidates = json.loads(stored_candidates)
                except json.JSONDecodeError:
                    candidates = []
                if candidates:
                    st.markdown("**Suggested Email Alternatives:**")
                    for candidate in candidates:
                        if st.button(
                            candidate,
                            key=f"candidate_{lead_id}_{candidate}",
                            type="secondary",
                        ):
                            db.update_lead(
                                lead_id,
                                verified_email=candidate,
                            )
                            st.success(
                                f"Verified email set to {candidate}."
                            )
                            st.rerun()

            if st.button(
                "Save Contact Info",
                key=f"save_info_{lead_id}",
                type="secondary",
            ):
                db.update_lead(
                    lead_id,
                    domain=edited_domain or None,
                    verified_email=edited_email or None,
                    contact_name=edited_contact or None,
                )
                st.success(
                    f"Contact info saved for {lead['company_name']}."
                )
                st.rerun()

            st.markdown("---")

            current_subject = lead.get("custom_subject") or ""
            edited_subject = st.text_input(
                "Subject Line",
                value=current_subject,
                placeholder="quick dev question",
                key=f"subject_{lead_id}",
            )

            current_pitch = lead.get("custom_pitch") or ""
            edited_pitch = st.text_area(
                "Generated Pitch (plain text only)",
                value=current_pitch,
                height=180,
                key=f"pitch_{lead_id}",
            )

            col_approve, col_skip, col_spacer = st.columns([1, 1, 3])

            with col_approve:
                if st.button(
                    "Approve & Queue",
                    key=f"approve_{lead_id}",
                    type="primary",
                ):
                    try:
                        # Persist the edited contact info and pitch first.
                        db.update_lead(
                            lead_id,
                            custom_pitch=edited_pitch,
                            custom_subject=edited_subject or None,
                            domain=edited_domain or None,
                            verified_email=edited_email or None,
                            contact_name=edited_contact or None,
                        )

                        # Deep SMTP mailbox verification guardrail.  Only
                        # auto-approve into the Cold Triage queue when the
                        # mailbox status is VERIFIED_DELIVERABLE or RISKY_CATCHALL/UNKNOWN_UNVERIFIED/UNVERIFIED_TIMEOUT.
                        # Invalid users are never queued.
                        if not edited_email:
                            st.error(
                                "Cannot queue a lead without a verified email. "
                                "Please provide an email address first."
                            )
                            st.rerun()

                        mailbox_result = verify_mailbox(edited_email)
                        mailbox_status = mailbox_result["status"]
                        db.update_lead(
                            lead_id,
                            mailbox_status=mailbox_status,
                        )

                        if mailbox_status == "INVALID_USER":
                            st.error(
                                f"Email {edited_email} is invalid. "
                                "This lead will NOT be queued. "
                                f"Reason: {mailbox_result['reason']}"
                            )
                            st.rerun()

                        # Status is VERIFIED_DELIVERABLE or RISKY/UNKNOWN/TIMEOUT — safe to auto-queue.
                        emailer = _get_emailer(db)
                        result = emailer.send_email_1(lead_id)
                        if result.success:
                            st.toast("Lead approved and queued!")
                            st.success(
                                f"Lead {lead['company_name']} queued for send. "
                                "Follow-up 1 scheduled."
                            )
                        else:
                            st.toast("Lead approved and queued!")
                            st.warning(
                                f"Lead {lead['company_name']} queued but "
                                f"dispatch deferred: {result.reason}"
                            )
                            db.mark_email_1_sent(lead_id)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Error during lead approval/dispatch: {exc}")

            with col_skip:
                if st.button("Skip", key=f"skip_{lead_id}"):
                    db.mark_skipped(lead_id)
                    st.warning(f"Lead {lead['company_name']} skipped.")
                    st.rerun()

        st.divider()


def render_followup_radar(db: Database) -> None:
    """Render the Follow-Up Radar tab.

    Surfaces leads whose follow-up due date has arrived or passed and
    provides 1-click execution for the 3-day bump and 10-day breakup
    messages.

    Args:
        db: The active database connection.
    """
    st.subheader("Follow-Up Radar")
    st.caption(
        "Leads surfaced here have a follow-up due today or overdue."
    )

    due_leads = db.get_followups_due_today()

    if not due_leads:
        st.success("No follow-ups due today. You are all caught up.")
        return

    for lead in due_leads:
        lead_id = int(lead["id"])
        status = lead.get("status", "")
        due_date = lead.get("followup_1_due_date") or lead.get(
            "followup_2_due_date"
        )

        render_lead_card(lead)

        with st.expander("Follow-Up Actions", expanded=False):
            st.caption(f"Due date: {due_date}")

            col_send, col_replied, col_meeting, col_bounced = st.columns(4)

            with col_send:
                if status == "FOLLOWUP_1_DUE":
                    button_label = "Send Follow-Up 1"
                    action = "followup_1"
                else:
                    button_label = "Send Breakup Email"
                    action = "breakup"

                if st.button(button_label, key=f"send_{lead_id}"):
                    emailer = _get_emailer(db)
                    if action == "followup_1":
                        result = emailer.send_followup_1(lead_id)
                    else:
                        result = emailer.send_breakup(lead_id)

                    if result.success:
                        st.success(
                            f"Follow-up sent for {lead['company_name']}."
                        )
                    else:
                        st.warning(
                            f"Dispatch deferred for {lead['company_name']}: "
                            f"{result.reason}"
                        )
                        if action == "followup_1":
                            db.mark_followup_1_sent(lead_id)
                        else:
                            db.mark_breakup_sent(lead_id)
                    st.rerun()

            with col_replied:
                if st.button("Mark Replied", key=f"replied_{lead_id}"):
                    db.mark_replied(lead_id)
                    st.success(f"{lead['company_name']} marked as replied.")
                    st.rerun()

            with col_meeting:
                if st.button(
                    "Meeting Booked", key=f"meeting_{lead_id}"
                ):
                    db.mark_meeting_booked(lead_id)
                    st.success(
                        f"Meeting booked for {lead['company_name']}."
                    )
                    st.rerun()

            with col_bounced:
                if st.button("Mark Bounced", key=f"bounced_{lead_id}"):
                    db.mark_bounced(lead_id)
                    st.warning(f"{lead['company_name']} marked as bounced.")
                    st.rerun()

        st.divider()


def _missing_fields(lead: dict[str, Any]) -> str:
    """Return a human-readable comma-separated list of missing fields.

    Args:
        lead: The lead dictionary to check.

    Returns:
        str: A description of which optional fields are missing,
            or ``"All fields present"``.
    """
    missing = []
    if not lead.get("domain"):
        missing.append("domain")
    if not lead.get("verified_email"):
        missing.append("verified_email")
    if not lead.get("contact_name"):
        missing.append("contact_name")
    if not missing:
        return "All fields present"
    return ", ".join(missing)


def render_master_ledger(db: Database) -> None:
    """Render the Master Ledger tab.

    Provides a searchable data grid of all leads with a Missing Fields
    column that highlights leads needing enrichment, plus manual status
    override controls.

    Args:
        db: The active database connection.
    """
    st.subheader("Master Ledger")
    st.caption(
        "Search all leads and manually override their status when needed."
    )

    col_search, col_filter = st.columns([3, 1])

    with col_search:
        search_term = st.text_input(
            "Search leads",
            placeholder="Search by company, domain, email, or contact...",
        )

    with col_filter:
        status_filter = st.selectbox(
            "Status filter",
            options=["ALL"] + list(LEAD_STATES),
        )

    leads = db.list_leads(
        search_term=search_term or None,
        status=None if status_filter == "ALL" else status_filter,
    )

    if not leads:
        st.info("No leads found matching the current filter.")
        return

    display_columns = [
        "id",
        "company_name",
        "domain",
        "verified_email",
        "mailbox_status",
        "contact_name",
        "status",
        "tech_stack",
        "created_at",
        "followup_1_due_date",
        "followup_2_due_date",
    ]

    table_data = [
        {col: lead.get(col) for col in display_columns} for lead in leads
    ]

    st.dataframe(
        table_data,
        use_container_width=True,
        hide_index=True,
    )

    # Display missing-field warnings below the table.
    st.markdown("#### Missing Fields Summary")
    missing_any = False
    for lead in leads:
        missing = _missing_fields(lead)
        if missing != "All fields present":
            missing_any = True
            badges = []
            if not lead.get("domain"):
                badges.append("**Missing Domain**")
            if not lead.get("verified_email"):
                badges.append("**Missing Email**")
            if not lead.get("contact_name"):
                badges.append("**Missing Contact**")
            st.markdown(
                f"- #{lead['id']} **{lead.get('company_name', '?')}** — "
                + ", ".join(badges)
            )
    if not missing_any:
        st.success("All displayed leads have complete contact info.")

    st.divider()
    st.markdown("### Manual Status Override")

    lead_options = {
        f"#{lead['id']} - {lead['company_name']} "
        f"({lead.get('domain') or 'no domain'})": int(lead["id"])
        for lead in leads
    }

    if not lead_options:
        return

    selected_label = st.selectbox(
        "Select lead",
        options=list(lead_options.keys()),
    )
    selected_id = lead_options[selected_label]

    current_lead = db.get_lead(selected_id)
    if current_lead is None:
        st.error("Selected lead no longer exists.")
        return

    current_status = current_lead.get("status", "UNPROCESSED")
    st.caption(f"Current status: {current_status}")

    new_status = st.selectbox(
        "New status",
        options=list(LEAD_STATES),
        index=list(LEAD_STATES).index(current_status)
        if current_status in LEAD_STATES
        else 0,
    )

    if st.button("Apply Status Override", type="primary"):
        db.update_lead(selected_id, status=new_status)
        st.success(
            f"Lead #{selected_id} status updated to {new_status}."
        )
        st.rerun()


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the Streamlit application."""
    st.set_page_config(
        page_title="B2B Substrate",
        page_icon="📡",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)

    config = load_config()
    db = _get_database()

    with st.sidebar:
        st.title("📡 B2B Substrate")
        st.caption("Lead Triage & Email Sequencer")

        st.divider()

        st.markdown("### Credential Status")
        if config.credentials.has_credentials:
            st.success(
                f"GCP credentials loaded from:\n\n"
                f"`{config.credentials.service_account_path}`"
            )
        else:
            st.warning(
                "No GCP service account credentials found. Set "
                "`GOOGLE_APPLICATION_CREDENTIALS` or place "
                "`service_account.json` in the project root."
            )

        st.divider()

        st.markdown("### SMTP Status")
        if config.smtp.is_configured:
            st.success(
                f"SMTP relay configured:\n\n"
                f"`{config.smtp.host}:{config.smtp.port}`"
            )
        else:
            st.warning(
                "SMTP relay not configured. Set SMTP_HOST, SMTP_PORT, "
                "SMTP_USERNAME, SMTP_PASSWORD, and SMTP_FROM_EMAIL."
            )

        st.divider()

        st.markdown("### Daily Send Cap")
        sent_today = db.get_sent_today_count()
        remaining = max(DAILY_SEND_CAP - sent_today, 0)
        st.progress(
            min(sent_today / DAILY_SEND_CAP, 1.0),
            text=f"{sent_today} / {DAILY_SEND_CAP} sent today",
        )
        st.caption(f"{remaining} emails remaining in today's cap.")

        st.divider()

    st.title("📡 B2B Substrate")
    st.caption(
        "Security-conscious B2B lead triage engine and email sequencer."
    )

    render_kpi_ribbon(db)

    st.divider()

    tab_ingest, tab_qualify, tab_triage, tab_followup, tab_ledger = st.tabs(
        [
            "📥 Ingestion",
            "🤖 LLM Qualification",
            "❄️ Cold Triage Desk",
            "📡 Follow-Up Radar",
            "📒 Master Ledger",
        ]
    )

    with tab_ingest:
        render_ingestion(db)

    with tab_qualify:
        render_llm_qualification(db)

    with tab_triage:
        render_triage_desk(db)

    with tab_followup:
        render_followup_radar(db)

    with tab_ledger:
        render_master_ledger(db)


if __name__ == "__main__":
    main()
