"""SQLite database layer for B2B Substrate.

This module provides the persistence layer for the B2B lead triage engine.
It manages the ``leads`` table with strict ``UNIQUE`` constraints on
``domain`` and ``verified_email`` to guarantee zero double-sends, and
implements the full lead state machine with automatic business-day
follow-up scheduling.

The module exposes a :class:`Database` class that wraps a SQLite connection
and provides all CRUD operations used by the Streamlit application layer.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from config import (
    BREAKUP_BUSINESS_DAYS,
    FOLLOWUP_1_BUSINESS_DAYS,
    LEAD_STATE_BOUNCED,
    LEAD_STATE_BREAKUP_SENT,
    LEAD_STATE_EMAIL_1_SENT,
    LEAD_STATE_FOLLOWUP_1_DUE,
    LEAD_STATE_FOLLOWUP_1_SENT,
    LEAD_STATE_FOLLOWUP_2_DUE,
    LEAD_STATE_MEETING_BOOKED,
    LEAD_STATE_QUALIFIED,
    LEAD_STATE_REPLIED,
    LEAD_STATE_SKIPPED,
    LEAD_STATE_UNPROCESSED,
    LEAD_STATES,
)

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

_SCHEMA_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    domain TEXT UNIQUE,
    verified_email TEXT UNIQUE,
    contact_name TEXT,
    title TEXT,
    tech_stack TEXT,
    website_text TEXT,
    sanitized_text TEXT,
    qualification_verdict TEXT,
    reasoning TEXT,
    custom_pitch TEXT,
    custom_subject TEXT,
    search_helpers TEXT,
    email_candidates TEXT,
    mailbox_status TEXT,
    status TEXT NOT NULL DEFAULT 'UNPROCESSED',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    email_1_sent_at TEXT,
    followup_1_due_date TEXT,
    followup_1_sent_at TEXT,
    followup_2_due_date TEXT,
    breakup_sent_at TEXT,
    replied_at TEXT,
    skipped_at TEXT,
    bounced_at TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_followup_1_due ON leads(followup_1_due_date);
CREATE INDEX IF NOT EXISTS idx_leads_followup_2_due ON leads(followup_2_due_date);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at);
"""

# Columns that may be written when inserting or updating a lead.
_LEAD_COLUMNS: Final[tuple[str, ...]] = (
    "company_name",
    "domain",
    "verified_email",
    "contact_name",
    "title",
    "tech_stack",
    "website_text",
    "sanitized_text",
    "qualification_verdict",
    "reasoning",
    "custom_pitch",
    "custom_subject",
    "search_helpers",
    "email_candidates",
    "mailbox_status",
    "status",
    "created_at",
    "updated_at",
    "email_1_sent_at",
    "followup_1_due_date",
    "followup_1_sent_at",
    "followup_2_due_date",
    "breakup_sent_at",
    "replied_at",
    "skipped_at",
    "bounced_at",
    "notes",
)

# ---------------------------------------------------------------------------
# Business-day helpers
# ---------------------------------------------------------------------------


def _is_weekend(day: date) -> bool:
    """Return ``True`` when the given date falls on a weekend.

    Args:
        day: The date to inspect.

    Returns:
        bool: ``True`` when the date is a Saturday or Sunday.
    """
    return day.weekday() >= 5


def add_business_days(start_date: date, business_days: int) -> date:
    """Add a number of business days to a starting date.

    Weekends (Saturday and Sunday) are skipped when advancing the calendar.
    The result is always a weekday.

    Args:
        start_date: The starting date.
        business_days: The number of business days to advance.  Must be
            non-negative.

    Returns:
        date: The resulting business date.

    Raises:
        ValueError: If ``business_days`` is negative.
    """
    if business_days < 0:
        raise ValueError("business_days must be non-negative")

    current = start_date
    remaining = business_days
    while remaining > 0:
        current += timedelta(days=1)
        if not _is_weekend(current):
            remaining -= 1
    return current


def today_iso() -> str:
    """Return today's date as an ISO-8601 string.

    Returns:
        str: The current local date in ``YYYY-MM-DD`` format.
    """
    return date.today().isoformat()


def now_iso() -> str:
    """Return the current timestamp as an ISO-8601 string.

    Returns:
        str: The current local datetime in ``YYYY-MM-DDTHH:MM:SS`` format.
    """
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class Database:
    """SQLite-backed persistence layer for B2B leads.

    The class manages a single SQLite connection, creates the schema on
    first use, and exposes CRUD operations for the lead lifecycle.

    Attributes:
        db_path: Absolute path to the SQLite database file.
        connection: The active :class:`sqlite3.Connection` instance.
    """

    def __init__(self, db_path: Path | str) -> None:
        """Initialize the database connection and create the schema.

        Args:
            db_path: Path to the SQLite database file.  Parent directories
                are created automatically if they do not exist.
        """
        self.db_path: Path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection: sqlite3.Connection = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _create_schema(self) -> None:
        """Create the leads table and supporting indexes if absent."""
        self.connection.executescript(_SCHEMA_SQL)
        self._migrate_schema()
        self.connection.commit()

    def _migrate_schema(self) -> None:
        """Apply additive migrations to existing databases.

        ``CREATE TABLE IF NOT EXISTS`` does not alter an existing table,
        so columns added after the initial release must be back-filled
        here.  Each migration is idempotent and safe to run on every
        startup.
        """
        columns = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(leads)")
        }
        if "custom_subject" not in columns:
            self.connection.execute(
                "ALTER TABLE leads ADD COLUMN custom_subject TEXT"
            )
        if "email_candidates" not in columns:
            self.connection.execute(
                "ALTER TABLE leads ADD COLUMN email_candidates TEXT"
            )
        if "mailbox_status" not in columns:
            self.connection.execute(
                "ALTER TABLE leads ADD COLUMN mailbox_status TEXT"
            )

    # ------------------------------------------------------------------
    # Row helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        """Convert a SQLite row to a plain dictionary.

        Args:
            row: A SQLite row, or ``None``.

        Returns:
            dict[str, Any] | None: The row as a dictionary, or ``None``
                when the input row is ``None``.
        """
        if row is None:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Lead CRUD
    # ------------------------------------------------------------------

    def insert_lead(
        self,
        *,
        company_name: str,
        domain: str | None = None,
        verified_email: str | None = None,
        contact_name: str | None = None,
        title: str | None = None,
        tech_stack: str | None = None,
        website_text: str | None = None,
        sanitized_text: str | None = None,
        qualification_verdict: str | None = None,
        reasoning: str | None = None,
        custom_pitch: str | None = None,
        custom_subject: str | None = None,
        search_helpers: str | None = None,
        email_candidates: str | None = None,
        notes: str | None = None,
    ) -> int:
        """Insert a new lead into the database.

        The lead is created in the ``UNPROCESSED`` state.  When ``domain``
        or ``verified_email`` are provided, the ``UNIQUE`` constraint
        ensures no duplicate values exist; both may be ``None`` for leads
        ingested without a domain or email (e.g. partial directory data).

        Args:
            company_name: The company name.  Strictly required.
            domain: The company domain.  May be ``None`` when not available
                in the source data.  When provided, must be unique.
            verified_email: The verified contact email.  May be ``None``
                when not available.  When provided, must be unique.
            contact_name: Optional contact person name.
            title: Optional contact job title.
            tech_stack: Optional parsed technology stack summary.
            website_text: Optional raw scraped website text.
            sanitized_text: Optional sanitized website text.
            qualification_verdict: Optional LLM qualification verdict.
            reasoning: Optional LLM reasoning text.
            custom_pitch: Optional generated custom pitch.
            custom_subject: Optional custom email subject line.
            notes: Optional free-form notes.

        Returns:
            int: The auto-generated primary key of the new lead.

        Raises:
            sqlite3.IntegrityError: If the domain or verified email already
                exists in the database.
        """
        now = now_iso()
        values: dict[str, Any] = {
            "company_name": company_name,
            "domain": domain,
            "verified_email": verified_email,
            "contact_name": contact_name,
            "title": title,
            "tech_stack": tech_stack,
            "website_text": website_text,
            "sanitized_text": sanitized_text,
            "qualification_verdict": qualification_verdict,
            "reasoning": reasoning,
            "custom_pitch": custom_pitch,
            "custom_subject": custom_subject,
            "search_helpers": search_helpers,
            "email_candidates": email_candidates,
            "mailbox_status": None,
            "status": LEAD_STATE_UNPROCESSED,
            "created_at": now,
            "updated_at": now,
            "email_1_sent_at": None,
            "followup_1_due_date": None,
            "followup_1_sent_at": None,
            "followup_2_due_date": None,
            "breakup_sent_at": None,
            "replied_at": None,
            "skipped_at": None,
            "bounced_at": None,
            "notes": notes,
        }
        columns = ", ".join(_LEAD_COLUMNS)
        placeholders = ", ".join("?" for _ in _LEAD_COLUMNS)
        sql = f"INSERT INTO leads ({columns}) VALUES ({placeholders})"
        cursor = self.connection.execute(
            sql, tuple(values[column] for column in _LEAD_COLUMNS)
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_lead(self, lead_id: int) -> dict[str, Any] | None:
        """Fetch a single lead by its primary key.

        Args:
            lead_id: The lead's primary key.

        Returns:
            dict[str, Any] | None: The lead as a dictionary, or ``None``
                when no lead with the given ID exists.
        """
        cursor = self.connection.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        )
        return self._row_to_dict(cursor.fetchone())

    def get_lead_by_domain(self, domain: str) -> dict[str, Any] | None:
        """Fetch a single lead by its unique domain.

        Args:
            domain: The company domain to look up.

        Returns:
            dict[str, Any] | None: The lead as a dictionary, or ``None``
                when no lead with the given domain exists.
        """
        cursor = self.connection.execute(
            "SELECT * FROM leads WHERE domain = ?", (domain,)
        )
        return self._row_to_dict(cursor.fetchone())

    def get_lead_by_email(self, verified_email: str) -> dict[str, Any] | None:
        """Fetch a single lead by its unique verified email.

        Args:
            verified_email: The verified email to look up.

        Returns:
            dict[str, Any] | None: The lead as a dictionary, or ``None``
                when no lead with the given email exists.
        """
        cursor = self.connection.execute(
            "SELECT * FROM leads WHERE verified_email = ?", (verified_email,)
        )
        return self._row_to_dict(cursor.fetchone())

    def list_leads(
        self,
        *,
        status: str | None = None,
        search_term: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List leads with optional filtering and search.

        Args:
            status: Optional status filter.  When provided, only leads in
                this state are returned.
            search_term: Optional case-insensitive search across company
                name, domain, verified email, and contact name.
            limit: Optional maximum number of rows to return.
            offset: Number of rows to skip before returning results.

        Returns:
            list[dict[str, Any]]: A list of lead dictionaries.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        if search_term:
            like = f"%{search_term}%"
            clauses.append(
                "(company_name LIKE ? OR domain LIKE ? OR "
                "verified_email LIKE ? OR contact_name LIKE ?)"
            )
            params.extend([like, like, like, like])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM leads {where} ORDER BY created_at DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        cursor = self.connection.execute(sql, tuple(params))
        return [self._row_to_dict(row) for row in cursor.fetchall()]

    def count_leads(self, *, status: str | None = None) -> int:
        """Count leads, optionally filtered by status.

        Args:
            status: Optional status filter.

        Returns:
            int: The number of matching leads.
        """
        if status is None:
            cursor = self.connection.execute("SELECT COUNT(*) FROM leads")
        else:
            cursor = self.connection.execute(
                "SELECT COUNT(*) FROM leads WHERE status = ?", (status,)
            )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def update_lead(self, lead_id: int, **fields: Any) -> bool:
        """Update arbitrary fields on a lead.

        The ``updated_at`` timestamp is always refreshed.  The ``id`` and
        ``created_at`` fields cannot be modified through this method.
        ``domain`` and ``verified_email`` may be updated so the UI can
        patch missing values discovered during triage.

        Args:
            lead_id: The lead's primary key.
            **fields: Column name/value pairs to update.

        Returns:
            bool: ``True`` when at least one row was updated.

        Raises:
            ValueError: If an attempt is made to update a protected field.
            sqlite3.IntegrityError: If the new ``domain`` or
                ``verified_email`` value already exists on another lead.
        """
        protected = {"id", "created_at"}
        invalid = protected.intersection(fields.keys())
        if invalid:
            raise ValueError(f"Cannot update protected fields: {sorted(invalid)}")

        if not fields:
            return False

        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [lead_id]
        cursor = self.connection.execute(
            f"UPDATE leads SET {assignments} WHERE id = ?", tuple(params)
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def delete_lead(self, lead_id: int) -> bool:
        """Delete a lead by its primary key.

        Args:
            lead_id: The lead's primary key.

        Returns:
            bool: ``True`` when a row was deleted.
        """
        cursor = self.connection.execute(
            "DELETE FROM leads WHERE id = ?", (lead_id,)
        )
        self.connection.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # State machine transitions
    # ------------------------------------------------------------------

    def _transition(
        self,
        lead_id: int,
        new_status: str,
        *,
        extra_fields: dict[str, Any] | None = None,
    ) -> bool:
        """Apply a state transition to a lead.

        Args:
            lead_id: The lead's primary key.
            new_status: The target state.
            extra_fields: Optional additional column updates applied
                atomically with the status change.

        Returns:
            bool: ``True`` when the transition was applied.
        """
        fields: dict[str, Any] = {"status": new_status}
        if extra_fields:
            fields.update(extra_fields)
        return self.update_lead(lead_id, **fields)

    def mark_qualified(self, lead_id: int) -> bool:
        """Transition a lead to the ``QUALIFIED`` state.

        Args:
            lead_id: The lead's primary key.

        Returns:
            bool: ``True`` when the transition was applied.
        """
        return self._transition(lead_id, LEAD_STATE_QUALIFIED)

    def mark_email_1_sent(self, lead_id: int) -> bool:
        """Mark the first cold email as sent and schedule follow-up 1.

        The follow-up 1 due date is computed as the current date plus
        ``FOLLOWUP_1_BUSINESS_DAYS`` business days.

        Args:
            lead_id: The lead's primary key.

        Returns:
            bool: ``True`` when the transition was applied.
        """
        sent_at = now_iso()
        due_date = add_business_days(
            date.today(), FOLLOWUP_1_BUSINESS_DAYS
        ).isoformat()
        return self._transition(
            lead_id,
            LEAD_STATE_EMAIL_1_SENT,
            extra_fields={
                "email_1_sent_at": sent_at,
                "followup_1_due_date": due_date,
            },
        )

    def mark_followup_1_due(self, lead_id: int) -> bool:
        """Transition a lead to the ``FOLLOWUP_1_DUE`` state.

        Args:
            lead_id: The lead's primary key.

        Returns:
            bool: ``True`` when the transition was applied.
        """
        return self._transition(lead_id, LEAD_STATE_FOLLOWUP_1_DUE)

    def mark_followup_1_sent(self, lead_id: int) -> bool:
        """Mark follow-up 1 as sent and schedule the breakup email.

        The breakup due date is computed as the current date plus
        ``BREAKUP_BUSINESS_DAYS`` business days.

        Args:
            lead_id: The lead's primary key.

        Returns:
            bool: ``True`` when the transition was applied.
        """
        sent_at = now_iso()
        due_date = add_business_days(
            date.today(), BREAKUP_BUSINESS_DAYS
        ).isoformat()
        return self._transition(
            lead_id,
            LEAD_STATE_FOLLOWUP_1_SENT,
            extra_fields={
                "followup_1_sent_at": sent_at,
                "followup_2_due_date": due_date,
            },
        )

    def mark_followup_2_due(self, lead_id: int) -> bool:
        """Transition a lead to the ``FOLLOWUP_2_DUE`` state.

        Args:
            lead_id: The lead's primary key.

        Returns:
            bool: ``True`` when the transition was applied.
        """
        return self._transition(lead_id, LEAD_STATE_FOLLOWUP_2_DUE)

    def mark_breakup_sent(self, lead_id: int) -> bool:
        """Mark the breakup email as sent.

        Args:
            lead_id: The lead's primary key.

        Returns:
            bool: ``True`` when the transition was applied.
        """
        return self._transition(
            lead_id,
            LEAD_STATE_BREAKUP_SENT,
            extra_fields={"breakup_sent_at": now_iso()},
        )

    def mark_replied(self, lead_id: int) -> bool:
        """Mark a lead as having replied.

        Args:
            lead_id: The lead's primary key.

        Returns:
            bool: ``True`` when the transition was applied.
        """
        return self._transition(
            lead_id,
            LEAD_STATE_REPLIED,
            extra_fields={"replied_at": now_iso()},
        )

    def mark_meeting_booked(self, lead_id: int) -> bool:
        """Mark a lead as having a meeting booked.

        Args:
            lead_id: The lead's primary key.

        Returns:
            bool: ``True`` when the transition was applied.
        """
        return self._transition(lead_id, LEAD_STATE_MEETING_BOOKED)

    def mark_skipped(self, lead_id: int) -> bool:
        """Mark a lead as skipped.

        Args:
            lead_id: The lead's primary key.

        Returns:
            bool: ``True`` when the transition was applied.
        """
        return self._transition(
            lead_id,
            LEAD_STATE_SKIPPED,
            extra_fields={"skipped_at": now_iso()},
        )

    def mark_bounced(self, lead_id: int) -> bool:
        """Mark a lead as bounced.

        Args:
            lead_id: The lead's primary key.

        Returns:
            bool: ``True`` when the transition was applied.
        """
        return self._transition(
            lead_id,
            LEAD_STATE_BOUNCED,
            extra_fields={"bounced_at": now_iso()},
        )

    # ------------------------------------------------------------------
    # Query helpers for the UI
    # ------------------------------------------------------------------

    def get_active_outreach_count(self) -> int:
        """Count leads currently in an active outreach state.

        Active states are ``EMAIL_1_SENT``, ``FOLLOWUP_1_DUE``,
        ``FOLLOWUP_1_SENT``, and ``FOLLOWUP_2_DUE``.

        Returns:
            int: The number of active outreach leads.
        """
        active_states = (
            LEAD_STATE_EMAIL_1_SENT,
            LEAD_STATE_FOLLOWUP_1_DUE,
            LEAD_STATE_FOLLOWUP_1_SENT,
            LEAD_STATE_FOLLOWUP_2_DUE,
        )
        placeholders = ", ".join("?" for _ in active_states)
        cursor = self.connection.execute(
            f"SELECT COUNT(*) FROM leads WHERE status IN ({placeholders})",
            active_states,
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def get_sent_today_count(self) -> int:
        """Count cold emails sent today.

        A lead counts as sent today when its ``email_1_sent_at`` timestamp
        falls on the current calendar date.

        Returns:
            int: The number of first emails sent today.
        """
        today = today_iso()
        cursor = self.connection.execute(
            "SELECT COUNT(*) FROM leads WHERE email_1_sent_at LIKE ?",
            (f"{today}%",),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def get_sent_in_window_count(self, window_hours: int = 24) -> int:
        """Count all cold emails dispatched in a rolling time window.

        The count includes first emails, follow-up 1 emails, and breakup
        emails whose dispatch timestamps fall within the last
        ``window_hours`` hours.

        Args:
            window_hours: The rolling time window in hours.  Defaults to
                24.

        Returns:
            int: The number of emails dispatched in the window.
        """
        cutoff = (
            datetime.now() - timedelta(hours=window_hours)
        ).isoformat(timespec="seconds")
        cursor = self.connection.execute(
            """
            SELECT COUNT(*) FROM leads
            WHERE (
                (email_1_sent_at IS NOT NULL AND email_1_sent_at >= ?)
                OR
                (followup_1_sent_at IS NOT NULL AND followup_1_sent_at >= ?)
                OR
                (breakup_sent_at IS NOT NULL AND breakup_sent_at >= ?)
            )
            """,
            (cutoff, cutoff, cutoff),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def get_followups_due_today(self) -> list[dict[str, Any]]:
        """Return leads whose follow-up is due on or before today.

        A lead is surfaced when its ``followup_1_due_date`` or
        ``followup_2_due_date`` is not null and is less than or equal to
        today's date, and the lead is still in a due state.

        Returns:
            list[dict[str, Any]]: A list of lead dictionaries that are
                due for a follow-up.
        """
        today = today_iso()
        cursor = self.connection.execute(
            """
            SELECT * FROM leads
            WHERE (
                (status = ? AND followup_1_due_date IS NOT NULL
                 AND followup_1_due_date <= ?)
                OR
                (status = ? AND followup_2_due_date IS NOT NULL
                 AND followup_2_due_date <= ?)
            )
            ORDER BY COALESCE(followup_1_due_date, followup_2_due_date) ASC
            """,
            (LEAD_STATE_FOLLOWUP_1_DUE, today, LEAD_STATE_FOLLOWUP_2_DUE, today),
        )
        return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_meetings_booked_count(self) -> int:
        """Count leads with a meeting booked.

        Returns:
            int: The number of leads in the ``MEETING_BOOKED`` state.
        """
        return self.count_leads(status=LEAD_STATE_MEETING_BOOKED)

    def get_unprocessed_leads(self) -> list[dict[str, Any]]:
        """Return all leads in the ``UNPROCESSED`` state.

        Returns:
            list[dict[str, Any]]: A list of unprocessed lead dictionaries.
        """
        return self.list_leads(status=LEAD_STATE_UNPROCESSED)

    def get_qualified_leads(self) -> list[dict[str, Any]]:
        """Return all leads in the ``QUALIFIED`` state.

        Returns:
            list[dict[str, Any]]: A list of qualified lead dictionaries.
        """
        return self.list_leads(status=LEAD_STATE_QUALIFIED)

    def get_skipped_leads(self) -> list[dict[str, Any]]:
        """Return all leads in the ``SKIPPED`` state.

        Returns:
            list[dict[str, Any]]: A list of skipped lead dictionaries.
        """
        return self.list_leads(status=LEAD_STATE_SKIPPED)

    def get_bounced_leads(self) -> list[dict[str, Any]]:
        """Return all leads in the ``BOUNCED`` state.

        Returns:
            list[dict[str, Any]]: A list of bounced lead dictionaries.
        """
        return self.list_leads(status=LEAD_STATE_BOUNCED)

    def get_replied_leads(self) -> list[dict[str, Any]]:
        """Return all leads in the ``REPLIED`` state.

        Returns:
            list[dict[str, Any]]: A list of replied lead dictionaries.
        """
        return self.list_leads(status=LEAD_STATE_REPLIED)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self.connection:
            self.connection.close()

    def __enter__(self) -> "Database":
        """Support the context manager protocol.

        Returns:
            Database: The current instance.
        """
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """Close the connection when exiting the context manager.

        Args:
            *exc_info: Exception information forwarded by the interpreter.
        """
        self.close()