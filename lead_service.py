"""Lead ingestion, deduplication, and lifecycle service layer.

This module is the single service boundary between the Streamlit UI
and the SQLAlchemy ORM models.  It exposes plain Python "endpoint
handler" functions — each documented and structured exactly as a REST
handler would be — for:

* Manual lead ingestion with pre-insert deduplication against
  ``leads.email`` and ``leads.website`` (mapped to the ``verified_email``
  and ``domain`` columns respectively).
* Strict status lifecycle transitions across the six-state machine
  defined in :mod:`config` (``QUALIFIED``, ``QUEUED``, ``SENT``,
  ``REPLIED``, ``DISQUALIFIED``, ``ARCHIVED``).
* Personalized draft generation for the Cold Triage desk via the
  Jinja2 interpolation engine in :mod:`templates_engine`.

No function in this module talks to an LLM, a web scraper, or an SMTP
relay — those dependencies have been fully purged from the backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from config import ALLOWED_TRANSITIONS, DEFAULT_LEAD_STATE, LEAD_STATES
from models import Lead
from templates_engine import LeadDraft, render_draft

# ---------------------------------------------------------------------------
# Errors and response payloads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorPayload:
    """A structured, JSON-serializable error response.

    Attributes:
        status_code: The HTTP-style status code the caller should
            surface (e.g. ``409`` for a conflict).
        error: A short machine-readable error code.
        detail: A human-readable explanation of the error.
        field: The name of the conflicting/invalid field, when
            applicable.
        value: The conflicting/invalid value, when applicable.
    """

    status_code: int
    error: str
    detail: str
    field: str | None = None
    value: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize this payload to a plain dictionary.

        Returns:
            dict[str, Any]: A JSON-serializable representation
                suitable for returning directly from a UI error
                handler.
        """
        return {
            "status_code": self.status_code,
            "error": self.error,
            "detail": self.detail,
            "field": self.field,
            "value": self.value,
        }


class DuplicateLeadError(Exception):
    """Raised when a manually ingested lead duplicates an existing row.

    Attributes:
        payload: The structured 409 Conflict error payload describing
            which field collided and with what value.
    """

    def __init__(self, *, field: str, value: str) -> None:
        """Initialize the duplicate-lead error.

        Args:
            field: The name of the field that already exists in the
                database (``"email"`` or ``"website"``).
            value: The conflicting value.
        """
        self.payload = ErrorPayload(
            status_code=409,
            error="DUPLICATE_LEAD",
            detail=(
                f"A lead with this {field} already exists in the "
                "database."
            ),
            field=field,
            value=value,
        )
        super().__init__(self.payload.detail)


class LeadNotFoundError(Exception):
    """Raised when a requested lead ID does not exist.

    Attributes:
        payload: The structured 404 Not Found error payload.
    """

    def __init__(self, lead_id: int) -> None:
        """Initialize the lead-not-found error.

        Args:
            lead_id: The primary key that could not be resolved.
        """
        self.payload = ErrorPayload(
            status_code=404,
            error="LEAD_NOT_FOUND",
            detail=f"No lead exists with id={lead_id}.",
            field="id",
            value=str(lead_id),
        )
        super().__init__(self.payload.detail)


class InvalidTransitionError(Exception):
    """Raised when a requested status transition is not permitted.

    Attributes:
        payload: The structured 400 Bad Request error payload.
    """

    def __init__(self, *, current_status: str, target_status: str) -> None:
        """Initialize the invalid-transition error.

        Args:
            current_status: The lead's current status.
            target_status: The requested (rejected) target status.
        """
        self.payload = ErrorPayload(
            status_code=400,
            error="INVALID_TRANSITION",
            detail=(
                f"Cannot transition a lead from {current_status!r} to "
                f"{target_status!r}."
            ),
            field="status",
            value=target_status,
        )
        super().__init__(self.payload.detail)


class UnknownStatusError(Exception):
    """Raised when a target status is not one of the six valid states.

    Attributes:
        payload: The structured 400 Bad Request error payload.
    """

    def __init__(self, target_status: str) -> None:
        """Initialize the unknown-status error.

        Args:
            target_status: The invalid status string supplied by the
                caller.
        """
        self.payload = ErrorPayload(
            status_code=400,
            error="UNKNOWN_STATUS",
            detail=(
                f"{target_status!r} is not a valid lead status. "
                f"Valid statuses are: {', '.join(LEAD_STATES)}."
            ),
            field="status",
            value=target_status,
        )
        super().__init__(self.payload.detail)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current local timestamp as an ISO-8601 string.

    Returns:
        str: The current datetime formatted as
            ``YYYY-MM-DDTHH:MM:SS``.
    """
    return datetime.now().isoformat(timespec="seconds")


def normalize_website(website: str | None) -> str | None:
    """Normalize a raw website value into a bare, comparable domain.

    Strips ``http(s)://`` schemes, a leading ``www.``, and any trailing
    path/query/fragment so that ``https://www.Example.com/pricing`` and
    ``example.com`` are recognized as the same deduplication key.

    Args:
        website: The raw website string supplied by the caller, or
            ``None``.

    Returns:
        str | None: The normalized, lowercased bare domain, or ``None``
            when ``website`` is empty/blank.
    """
    if not website or not website.strip():
        return None

    candidate = website.strip().lower()
    if "://" not in candidate:
        candidate = f"//{candidate}"
    parsed = urlparse(candidate)
    domain = parsed.netloc or parsed.path.split("/", 1)[0]
    domain = domain.split("/", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.rstrip(".") or None


def normalize_email(email: str | None) -> str | None:
    """Normalize a raw email value for consistent comparison/storage.

    Args:
        email: The raw email string supplied by the caller, or
            ``None``.

    Returns:
        str | None: The trimmed, lowercased email, or ``None`` when
            ``email`` is empty/blank.
    """
    if not email or not email.strip():
        return None
    return email.strip().lower()


# ---------------------------------------------------------------------------
# Manual ingestion & deduplication
# ---------------------------------------------------------------------------


def create_lead(
    session: Session,
    *,
    company_name: str,
    contact_name: str | None = None,
    website: str | None = None,
    contact_title: str | None = None,
    email: str | None = None,
    tech_stack: str | None = None,
    notes: str | None = None,
) -> Lead:
    """Handle a manual lead ingestion request.

    Performs pre-insert deduplication against ``leads.verified_email``
    (the ``email`` field) and ``leads.domain`` (the ``website`` field)
    before creating the row.  New leads are always created with the
    default status ``QUALIFIED``.

    Args:
        session: The active SQLAlchemy session.
        company_name: The company name. Strictly required.
        contact_name: The contact person's full name.
        website: The company website URL or bare domain.
        contact_title: The contact person's job title.
        email: The contact's email address.
        tech_stack: A free-text technology stack summary.
        notes: Free-form notes about the lead.

    Returns:
        Lead: The newly created, persisted lead row.

    Raises:
        DuplicateLeadError: If ``email`` or ``website`` already exists
            on another lead. Carries a structured 409 Conflict payload
            in :attr:`DuplicateLeadError.payload`.
    """
    normalized_email = normalize_email(email)
    normalized_domain = normalize_website(website)

    if normalized_email is not None:
        existing = session.execute(
            select(Lead).where(Lead.verified_email == normalized_email)
        ).scalar_one_or_none()
        if existing is not None:
            raise DuplicateLeadError(field="email", value=normalized_email)

    if normalized_domain is not None:
        existing = session.execute(
            select(Lead).where(Lead.domain == normalized_domain)
        ).scalar_one_or_none()
        if existing is not None:
            raise DuplicateLeadError(field="website", value=normalized_domain)

    now = _now_iso()
    lead = Lead(
        company_name=company_name.strip(),
        domain=normalized_domain,
        verified_email=normalized_email,
        contact_name=contact_name.strip() if contact_name else None,
        title=contact_title.strip() if contact_title else None,
        tech_stack=tech_stack.strip() if tech_stack else None,
        notes=notes.strip() if notes else None,
        status=DEFAULT_LEAD_STATE,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.flush()
    return lead


# ---------------------------------------------------------------------------
# Read/query handlers
# ---------------------------------------------------------------------------


def get_lead(session: Session, lead_id: int) -> Lead | None:
    """Fetch a single lead by primary key.

    Args:
        session: The active SQLAlchemy session.
        lead_id: The lead's primary key.

    Returns:
        Lead | None: The lead row, or ``None`` when no lead with the
            given ID exists.
    """
    return session.get(Lead, lead_id)


def list_leads(
    session: Session,
    *,
    status: str | None = None,
    search_term: str | None = None,
) -> list[Lead]:
    """List leads with optional status filtering and text search.

    Args:
        session: The active SQLAlchemy session.
        status: Optional status filter.  When provided, only leads in
            this exact status are returned.
        search_term: Optional case-insensitive search across company
            name, domain, verified email, and contact name.

    Returns:
        list[Lead]: The matching leads, most recently created first.
    """
    stmt = select(Lead)

    if status is not None:
        stmt = stmt.where(Lead.status == status)

    if search_term:
        like = f"%{search_term}%"
        stmt = stmt.where(
            or_(
                Lead.company_name.ilike(like),
                Lead.domain.ilike(like),
                Lead.verified_email.ilike(like),
                Lead.contact_name.ilike(like),
            )
        )

    stmt = stmt.order_by(Lead.created_at.desc())
    return list(session.execute(stmt).scalars().all())


def count_all_leads(session: Session) -> int:
    """Count every lead row regardless of status.

    This intentionally counts across *all* statuses, including any
    historical/legacy status values (e.g. rows created by a previous
    version of this application) that fall outside the current
    six-state lifecycle defined in :data:`config.LEAD_STATES`.

    Args:
        session: The active SQLAlchemy session.

    Returns:
        int: The total number of rows in the ``leads`` table.
    """
    return int(session.execute(select(func.count(Lead.id))).scalar_one())


# ---------------------------------------------------------------------------
# Status lifecycle
# ---------------------------------------------------------------------------


def transition_lead_status(
    session: Session, lead_id: int, target_status: str
) -> Lead:
    """Handle a lead status transition request.

    Enforces the transition graph defined in
    :data:`config.ALLOWED_TRANSITIONS`.  ``UNPROCESSED`` is not a
    recognized status anywhere in this application.

    Args:
        session: The active SQLAlchemy session.
        lead_id: The lead's primary key.
        target_status: The requested target status. Must be one of
            :data:`config.LEAD_STATES`.

    Returns:
        Lead: The updated lead row.

    Raises:
        LeadNotFoundError: If no lead with ``lead_id`` exists.
        UnknownStatusError: If ``target_status`` is not one of the six
            valid lifecycle states.
        InvalidTransitionError: If the transition from the lead's
            current status to ``target_status`` is not permitted.
    """
    if target_status not in LEAD_STATES:
        raise UnknownStatusError(target_status)

    lead = session.get(Lead, lead_id)
    if lead is None:
        raise LeadNotFoundError(lead_id)

    current_status = lead.status
    if current_status == target_status:
        return lead

    allowed = ALLOWED_TRANSITIONS.get(current_status, frozenset())
    if target_status not in allowed:
        raise InvalidTransitionError(
            current_status=current_status, target_status=target_status
        )

    lead.status = target_status
    lead.updated_at = _now_iso()
    session.flush()
    return lead


# ---------------------------------------------------------------------------
# Draft generation (Cold Triage desk)
# ---------------------------------------------------------------------------


def generate_lead_draft(session: Session, lead_id: int) -> LeadDraft:
    """Handle a Cold Triage desk draft-generation request.

    Renders a default personalized subject/body draft using the
    lead's ``contact_name`` (first name extracted) and ``tech_stack``,
    then persists the result onto ``custom_subject``/``custom_pitch``
    so the triage desk can display and further edit it.

    Args:
        session: The active SQLAlchemy session.
        lead_id: The lead's primary key.

    Returns:
        LeadDraft: The rendered subject, body, and extracted first
            name.

    Raises:
        LeadNotFoundError: If no lead with ``lead_id`` exists.
    """
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise LeadNotFoundError(lead_id)

    draft = render_draft(
        company_name=lead.company_name,
        contact_name=lead.contact_name,
        tech_stack=lead.tech_stack,
    )

    lead.custom_subject = draft.subject
    lead.custom_pitch = draft.body
    lead.updated_at = _now_iso()
    session.flush()
    return draft
