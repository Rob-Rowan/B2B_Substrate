"""Application configuration for B2B Substrate.

This module centralizes configuration values used across the B2B
Substrate backend: the SQLite database path, the lead status
lifecycle constants, the allowed status transition graph, and the
daily outreach send cap enforced by the Cold Triage Desk's live SMTP
dispatch flow.

All Vertex AI / LLM credential resolution, disposable-domain
blocklists, prompt-injection signatures, and bulk ingestion timeouts
have been removed as part of the backend purge — this application no
longer depends on any external LLM provider or web-scraping stack.
The Google Workspace SMTP dispatch engine itself lives in
:mod:`email_service`; this module only defines the daily send cap
that gates it.
"""


from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

BASE_DIR: Final[Path] = Path(__file__).resolve().parent

# The existing production SQLite database file.  This path is never
# altered by this application beyond additive row inserts/updates —
# no destructive schema operations are ever performed against it.
DATABASE_PATH: Final[Path] = Path(
    os.getenv("DATABASE_PATH", str(BASE_DIR / "leads.db"))
).resolve()

# ---------------------------------------------------------------------------
# Lead status lifecycle
# ---------------------------------------------------------------------------

LEAD_STATE_QUALIFIED: Final[str] = "QUALIFIED"
LEAD_STATE_QUEUED: Final[str] = "QUEUED"
LEAD_STATE_SENT: Final[str] = "SENT"
LEAD_STATE_REPLIED: Final[str] = "REPLIED"
LEAD_STATE_DISQUALIFIED: Final[str] = "DISQUALIFIED"
LEAD_STATE_ARCHIVED: Final[str] = "ARCHIVED"

LEAD_STATES: Final[tuple[str, ...]] = (
    LEAD_STATE_QUALIFIED,
    LEAD_STATE_QUEUED,
    LEAD_STATE_SENT,
    LEAD_STATE_REPLIED,
    LEAD_STATE_DISQUALIFIED,
    LEAD_STATE_ARCHIVED,
)

# The default status assigned to every newly ingested lead.
DEFAULT_LEAD_STATE: Final[str] = LEAD_STATE_QUALIFIED

# ---------------------------------------------------------------------------
# Outreach dispatch limits
# ---------------------------------------------------------------------------

#: The maximum number of live cold-outreach emails the Cold Triage
#: Desk is permitted to dispatch per calendar day. Overridable via the
#: ``DAILY_SEND_CAP`` environment variable, and further adjustable at
#: runtime from the Streamlit sidebar.
DAILY_SEND_CAP: Final[int] = int(os.getenv("DAILY_SEND_CAP", "20"))


# ---------------------------------------------------------------------------
# Status transition graph
# ---------------------------------------------------------------------------

# Maps each status to the set of statuses it may legally transition to.
# This is the single source of truth for lifecycle enforcement in
# ``lead_service.transition_lead_status``.  ``UNPROCESSED`` is
# intentionally absent everywhere in this application.
ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    LEAD_STATE_QUALIFIED: frozenset(
        {LEAD_STATE_QUEUED, LEAD_STATE_DISQUALIFIED, LEAD_STATE_ARCHIVED}
    ),
    LEAD_STATE_QUEUED: frozenset(
        {
            LEAD_STATE_QUALIFIED,
            LEAD_STATE_SENT,
            LEAD_STATE_DISQUALIFIED,
            LEAD_STATE_ARCHIVED,
        }
    ),
    LEAD_STATE_SENT: frozenset({LEAD_STATE_REPLIED, LEAD_STATE_ARCHIVED}),
    LEAD_STATE_REPLIED: frozenset({LEAD_STATE_ARCHIVED}),
    LEAD_STATE_DISQUALIFIED: frozenset(
        {LEAD_STATE_QUALIFIED, LEAD_STATE_ARCHIVED}
    ),
    LEAD_STATE_ARCHIVED: frozenset({LEAD_STATE_QUALIFIED}),
}

# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------


class AppConfig(BaseModel):
    """Top-level application configuration container.

    Attributes:
        database_path: Absolute path to the existing SQLite database
            file.  Never dropped, re-created, or schema-altered by this
            application.
    """

    database_path: Path = Field(
        default=DATABASE_PATH,
        description="Absolute path to the SQLite database file.",
    )


def load_config() -> AppConfig:
    """Load and return the application configuration.

    Returns:
        AppConfig: A fully populated application configuration object.
    """
    return AppConfig()
