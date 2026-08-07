"""Streamlit UI layer for B2B Substrate.

This module provides the Streamlit front-end for the B2B lead triage
engine.  It renders a dark slate/charcoal developer theme, a top KPI
ribbon, and three functional tabs:

1. Manual Ingestion — add a single lead via the clean manual-entry
   form, with pre-insert deduplication against ``email`` and
   ``website``.
2. Cold Triage Desk — review ``QUALIFIED`` leads, generate a default
   personalized draft via the Jinja2 interpolation engine, edit it,
   and move the lead through the status lifecycle.
3. Master Ledger — a searchable data grid of every lead with manual
   status-override controls constrained to the legal transition graph.

This UI layer talks exclusively to :mod:`lead_service`, which in turn
talks exclusively to the SQLAlchemy ORM in :mod:`models` through the
session factory in :mod:`database`.  There is no LLM provider, no web
scraper, and no outbound SMTP relay anywhere in this application.
"""

from __future__ import annotations

from typing import Any, Final

import streamlit as st

from config import ALLOWED_TRANSITIONS, LEAD_STATES, load_config
from database import get_session, init_db
from lead_service import (
    DuplicateLeadError,
    InvalidTransitionError,
    LeadNotFoundError,
    UnknownStatusError,
    count_all_leads,
    create_lead,
    list_leads,
    transition_lead_status,
)
from models import Lead
from templates_engine import render_draft

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

    .status-badge {{
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 4px;
        font-size: 0.7rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.03em;
    }}

    .status-QUALIFIED {{ background-color: #1E3A5F; color: #93C5FD; }}
    .status-QUEUED {{ background-color: #5C3A00; color: #FCD34D; }}
    .status-SENT {{ background-color: #1B4332; color: #95D5B2; }}
    .status-REPLIED {{ background-color: #14532D; color: #86EFAC; }}
    .status-DISQUALIFIED {{ background-color: #3B2F2F; color: #E7C6C6; }}
    .status-ARCHIVED {{ background-color: #374151; color: #9CA3AF; }}

    .error-badge {{
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
</style>
"""


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def render_status_badge(status: str) -> str:
    """Return an HTML status badge for a lead state.

    Args:
        status: The lead's current status string.

    Returns:
        str: An HTML span with the status badge styling.  Unknown
            legacy statuses (e.g. historical ``UNPROCESSED`` rows)
            still render using a generic badge class so the UI never
            crashes on old data.
    """
    safe_status = status.replace(" ", "_")
    return f'<span class="status-badge status-{safe_status}">{status}</span>'


def lead_to_dict(lead: Lead) -> dict[str, Any]:
    """Convert a :class:`Lead` ORM instance into a plain dictionary.

    Args:
        lead: The ORM lead instance to convert.

    Returns:
        dict[str, Any]: A dictionary of the lead's display-relevant
            columns.
    """
    return {
        "id": lead.id,
        "company_name": lead.company_name,
        "domain": lead.domain,
        "verified_email": lead.verified_email,
        "contact_name": lead.contact_name,
        "title": lead.title,
        "tech_stack": lead.tech_stack,
        "status": lead.status,
        "custom_subject": lead.custom_subject,
        "custom_pitch": lead.custom_pitch,
        "notes": lead.notes,
        "created_at": lead.created_at,
        "updated_at": lead.updated_at,
    }


def render_lead_card(lead_dict: dict[str, Any]) -> None:
    """Render a single lead as a styled card.

    Args:
        lead_dict: The lead dictionary to display, as produced by
            :func:`lead_to_dict`.
    """
    company = lead_dict.get("company_name", "Unknown Company")
    domain = lead_dict.get("domain") or ""
    email = lead_dict.get("verified_email") or ""
    contact = lead_dict.get("contact_name") or "Unknown Contact"
    title = lead_dict.get("title") or ""
    status = lead_dict.get("status", "QUALIFIED")
    tech_stack = lead_dict.get("tech_stack") or "Not recorded."

    meta_parts = [f"<b>{contact}</b>"]
    if title:
        meta_parts.append(title)
    if domain:
        meta_parts.append(domain)
    if email:
        meta_parts.append(email)
    meta_html = " &middot; ".join(meta_parts)

    st.markdown(
        f"""
        <div class="lead-card">
            <div class="lead-title">
                {company} {render_status_badge(status)}
            </div>
            <div class="lead-meta">{meta_html}</div>
            <div class="lead-meta"><b>Tech Stack:</b> {tech_stack}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------


def render_kpi_ribbon() -> None:
    """Render the top KPI ribbon with live counts per lifecycle status.

    The "Total Leads" card reflects every row in the ``leads`` table
    (including any historical/legacy status values), while the
    per-status cards reflect only the six current lifecycle states.
    """
    with get_session() as session:
        counts = {
            status: len(list_leads(session, status=status))
            for status in LEAD_STATES
        }
        total = count_all_leads(session)

    columns = st.columns(len(LEAD_STATES) + 1)
    with columns[0]:
        st.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-label">Total Leads</div>
                <div class="kpi-value">{total}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    for column, status in zip(columns[1:], LEAD_STATES):
        with column:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">{status}</div>
                    <div class="kpi-value">{counts[status]}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_manual_ingestion() -> None:
    """Render the manual lead ingestion form.

    Handles the manual ingestion endpoint contract: ``company_name``,
    ``contact_name``, ``website``, ``contact_title``, ``email``,
    ``tech_stack``, and ``notes``.  On a duplicate ``email`` or
    ``website``, a clear 409 Conflict-style error payload is rendered.
    """
    st.subheader("Manual Lead Ingestion")
    st.caption(
        "Add a single lead. New leads are always created with status "
        "QUALIFIED. Duplicate emails or websites are rejected."
    )

    with st.form("manual_lead_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            company_name = st.text_input("Company Name *")
            website = st.text_input(
                "Website", placeholder="https://example.com"
            )
            email = st.text_input(
                "Email", placeholder="contact@example.com"
            )
            tech_stack = st.text_input(
                "Tech Stack", placeholder="Django, React, PostgreSQL"
            )
        with col2:
            contact_name = st.text_input("Contact Name")
            contact_title = st.text_input("Contact Title")
            notes = st.text_area("Notes", height=100)

        submitted = st.form_submit_button("Add Lead", type="primary")

    if submitted:
        if not company_name.strip():
            st.error("Company Name is required.")
            return

        with get_session() as session:
            try:
                lead = create_lead(
                    session,
                    company_name=company_name,
                    contact_name=contact_name or None,
                    website=website or None,
                    contact_title=contact_title or None,
                    email=email or None,
                    tech_stack=tech_stack or None,
                    notes=notes or None,
                )
                st.success(
                    f"Lead #{lead.id} — {lead.company_name} — created "
                    f"with status QUALIFIED."
                )
            except DuplicateLeadError as exc:
                payload = exc.payload.to_dict()
                st.markdown(
                    f'<span class="error-badge">409 CONFLICT</span> '
                    f"**{payload['error']}**: {payload['detail']}",
                    unsafe_allow_html=True,
                )
                st.json(payload)


def render_triage_desk() -> None:
    """Render the Cold Triage Desk tab.

    Presents a lead-selector dropdown scoped **exclusively** to leads
    with ``status == "QUALIFIED"``. Selecting a lead auto-populates a
    personalized email pitch draft (contact first name + tech stack
    interpolation) via the deterministic Jinja2 template engine in
    :mod:`templates_engine`, fully editable before queuing. The
    "Queue Lead" action is the only lifecycle mutation available on
    this desk: it persists the edited subject/body and transitions the
    lead directly to ``QUEUED``. There is no LLM reasoning/evaluation
    output anywhere on this desk, and no background AI evaluation loop
    is ever triggered by this application.
    """
    st.subheader("Cold Triage Desk")
    st.caption(
        "Select a QUALIFIED lead, review the auto-populated pitch "
        "draft, edit it if needed, and queue it for outreach."
    )

    with get_session() as session:
        qualified_leads = [
            lead_to_dict(lead)
            for lead in list_leads(session, status="QUALIFIED")
        ]

    if not qualified_leads:
        st.info("No QUALIFIED leads awaiting triage.")
        return

    lead_options = {
        f"#{lead['id']} - {lead['company_name']} "
        f"({lead.get('domain') or 'no domain'})": lead
        for lead in qualified_leads
    }
    selected_label = st.selectbox(
        "Select QUALIFIED lead", options=list(lead_options.keys())
    )
    lead_dict = lead_options[selected_label]
    lead_id = int(lead_dict["id"])

    render_lead_card(lead_dict)

    subject_key = f"triage_subject_{lead_id}"
    body_key = f"triage_body_{lead_id}"

    if subject_key not in st.session_state or body_key not in st.session_state:
        draft = render_draft(
            company_name=lead_dict["company_name"],
            contact_name=lead_dict.get("contact_name"),
            tech_stack=lead_dict.get("tech_stack"),
        )
        st.session_state[subject_key] = (
            lead_dict.get("custom_subject") or draft.subject
        )
        st.session_state[body_key] = (
            lead_dict.get("custom_pitch") or draft.body
        )

    edited_subject = st.text_input("Subject", key=subject_key)
    edited_body = st.text_area("Body", height=220, key=body_key)

    col_regen, col_queue = st.columns([1, 1])
    with col_regen:
        if st.button("Regenerate Draft", key=f"regen_{lead_id}"):
            draft = render_draft(
                company_name=lead_dict["company_name"],
                contact_name=lead_dict.get("contact_name"),
                tech_stack=lead_dict.get("tech_stack"),
            )
            st.session_state[subject_key] = draft.subject
            st.session_state[body_key] = draft.body
            st.rerun()
    with col_queue:
        if st.button("Queue Lead", key=f"queue_{lead_id}", type="primary"):
            with get_session() as session:
                lead = session.get(Lead, lead_id)
                if lead is not None:
                    lead.custom_subject = edited_subject or None
                    lead.custom_pitch = edited_body or None
                try:
                    transition_lead_status(session, lead_id, "QUEUED")
                    st.success(f"Lead #{lead_id} queued for outreach.")
                except (
                    LeadNotFoundError,
                    UnknownStatusError,
                    InvalidTransitionError,
                ) as exc:
                    st.error(exc.payload.detail)
            st.session_state.pop(subject_key, None)
            st.session_state.pop(body_key, None)
            st.rerun()


def render_master_ledger() -> None:
    """Render the Master Ledger tab.

    Provides a searchable data grid of all leads plus manual status
    override controls constrained to the legal transition graph
    defined in :data:`config.ALLOWED_TRANSITIONS`.
    """
    st.subheader("Master Ledger")
    st.caption("Search all leads and manually override their status.")

    col_search, col_filter = st.columns([3, 1])
    with col_search:
        search_term = st.text_input(
            "Search leads",
            placeholder="Search by company, domain, email, or contact...",
        )
    with col_filter:
        status_filter = st.selectbox(
            "Status filter", options=["ALL"] + list(LEAD_STATES)
        )

    with get_session() as session:
        leads = [
            lead_to_dict(lead)
            for lead in list_leads(
                session,
                search_term=search_term or None,
                status=None if status_filter == "ALL" else status_filter,
            )
        ]

    if not leads:
        st.info("No leads found matching the current filter.")
        return

    display_columns = [
        "id",
        "company_name",
        "domain",
        "verified_email",
        "contact_name",
        "status",
        "tech_stack",
        "created_at",
    ]
    table_data = [
        {col: lead.get(col) for col in display_columns} for lead in leads
    ]
    st.dataframe(table_data, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("### Manual Status Override")

    lead_options = {
        f"#{lead['id']} - {lead['company_name']} "
        f"({lead.get('domain') or 'no domain'})": int(lead["id"])
        for lead in leads
    }
    selected_label = st.selectbox(
        "Select lead", options=list(lead_options.keys())
    )
    selected_id = lead_options[selected_label]

    with get_session() as session:
        current_lead = session.get(Lead, selected_id)
        current_status = current_lead.status if current_lead else "QUALIFIED"

    st.caption(f"Current status: {current_status}")

    allowed_targets = sorted(
        ALLOWED_TRANSITIONS.get(current_status, frozenset())
    )
    if not allowed_targets:
        st.warning(
            f"No forward transitions are defined for status "
            f"{current_status!r} (likely a legacy historical status)."
        )
        return

    new_status = st.selectbox("New status", options=allowed_targets)

    if st.button("Apply Status Override", type="primary"):
        with get_session() as session:
            try:
                transition_lead_status(session, selected_id, new_status)
                st.success(f"Lead #{selected_id} moved to {new_status}.")
            except (
                LeadNotFoundError,
                UnknownStatusError,
                InvalidTransitionError,
            ) as exc:
                st.error(exc.payload.detail)
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

    load_config()
    init_db()

    with st.sidebar:
        st.title("📡 B2B Substrate")
        st.caption("Manual Lead Triage & Status Lifecycle")
        st.divider()
        st.markdown("### Status Lifecycle")
        st.caption(" → ".join(LEAD_STATES))

    st.title("📡 B2B Substrate")
    st.caption("Manual lead ingestion, triage, and lifecycle tracking.")

    render_kpi_ribbon()

    st.divider()

    tab_ingest, tab_triage, tab_ledger = st.tabs(
        ["📥 Manual Ingestion", "❄️ Cold Triage Desk", "📒 Master Ledger"]
    )

    with tab_ingest:
        render_manual_ingestion()

    with tab_triage:
        render_triage_desk()

    with tab_ledger:
        render_master_ledger()


if __name__ == "__main__":
    main()
