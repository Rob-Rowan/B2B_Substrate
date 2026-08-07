"""SQLAlchemy ORM models for B2B Substrate.

This module defines SQLAlchemy Declarative Base models mapped
explicitly to the pre-existing ``leads`` and ``lead_touches`` SQLite
tables.  Every column mirrors the existing schema exactly (verified via
``PRAGMA table_info``) so that mapping this ORM layer onto the
production database requires zero ``ALTER TABLE`` operations and never
touches existing rows.

The one-to-many relationship ``Lead.touches`` <-> ``LeadTouch.lead`` is
declared with ``cascade="all, delete-orphan"`` so that deleting a lead
through the ORM also removes its dependent touch records, mirroring the
``ON DELETE CASCADE`` foreign key already present on ``lead_touches``.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """Declarative base class shared by all ORM models."""


class Lead(Base):
    """ORM mapping for the existing ``leads`` table.

    Every column below matches the existing SQLite schema column for
    column.  Columns prefixed with legacy outreach-sequence semantics
    (e.g. ``email_1_sent_at``, ``followup_1_due_date``) are retained
    purely for backward read/write compatibility with historical rows
    created by the previous LLM/scraper-based pipeline; the current
    application no longer writes to them for newly created leads.

    Attributes:
        id: Primary key.
        company_name: Company name (required).
        domain: Company website/domain. The manual-ingestion endpoint's
            ``website`` field is normalized and stored here. Unique
            when present.
        verified_email: Verified contact email. The manual-ingestion
            endpoint's ``email`` field is stored here. Unique when
            present.
        contact_name: Contact person's full name.
        title: Contact job title. The manual-ingestion endpoint's
            ``contact_title`` field is stored here.
        tech_stack: Technology stack summary supplied at ingestion.
        website_text: Legacy raw scraped website text (historical rows
            only).
        sanitized_text: Legacy sanitized website text (historical rows
            only).
        qualification_verdict: Legacy LLM qualification verdict
            (historical rows only).
        reasoning: Legacy LLM reasoning text (historical rows only).
        custom_pitch: The current outreach draft/pitch body, generated
            by the template interpolation engine and editable by the
            user before send.
        custom_subject: The current outreach draft subject line.
        search_helpers: Legacy LLM search-helper text (historical rows
            only).
        status: Current lifecycle status. New leads are always created
            as ``QUALIFIED``. Valid forward statuses are ``QUALIFIED``,
            ``QUEUED``, ``SENT``, ``REPLIED``, ``DISQUALIFIED``, and
            ``ARCHIVED``. Historical rows may retain legacy status
            values (e.g. ``UNPROCESSED``) which are never rewritten by
            this application.
        created_at: ISO-8601 creation timestamp.
        updated_at: ISO-8601 last-update timestamp.
        email_1_sent_at: Legacy first-email dispatch timestamp.
        followup_1_due_date: Legacy follow-up 1 due date.
        followup_1_sent_at: Legacy follow-up 1 dispatch timestamp.
        followup_2_due_date: Legacy follow-up 2 due date.
        breakup_sent_at: Legacy breakup dispatch timestamp.
        replied_at: Reply timestamp.
        skipped_at: Legacy skip timestamp.
        bounced_at: Legacy bounce timestamp.
        notes: Free-form notes.
        email_candidates: Legacy JSON-encoded email candidate list.
        mailbox_status: Legacy deep SMTP mailbox verification status.
        touches: One-to-many relationship to :class:`LeadTouch` records
            representing every outreach touch logged against this
            lead.
    """

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[Optional[str]] = mapped_column(
        Text, unique=True, nullable=True
    )
    verified_email: Mapped[Optional[str]] = mapped_column(
        Text, unique=True, nullable=True
    )
    contact_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tech_stack: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    website_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sanitized_text: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    qualification_verdict: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    custom_pitch: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    custom_subject: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    search_helpers: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="QUALIFIED"
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    email_1_sent_at: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    followup_1_due_date: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    followup_1_sent_at: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    followup_2_due_date: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    breakup_sent_at: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    replied_at: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    skipped_at: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bounced_at: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_candidates: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    mailbox_status: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    touches: Mapped[list["LeadTouch"]] = relationship(
        "LeadTouch",
        back_populates="lead",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="LeadTouch.id",
    )

    def __repr__(self) -> str:
        """Return a concise debug representation.

        Returns:
            str: A representation including id, company name, and
                status.
        """
        return (
            f"<Lead id={self.id} company_name={self.company_name!r} "
            f"status={self.status!r}>"
        )


class LeadTouch(Base):
    """ORM mapping for the existing ``lead_touches`` table.

    Attributes:
        id: Primary key.
        lead_id: Foreign key to :attr:`Lead.id`, cascades on delete.
        touch_type: The type of touch (e.g. ``EMAIL``, ``NOTE``).
        subject: Optional subject line for email-type touches.
        body: The touch body/content.
        status: The touch status (e.g. ``DRAFT``, ``SENT``,
            ``FAILED``).
        sent_at: ISO-8601 dispatch timestamp, or ``None`` when the
            touch has not been dispatched.
        created_at: ISO-8601 creation timestamp.
        lead: Many-to-one relationship back to the parent
            :class:`Lead`.
    """

    __tablename__ = "lead_touches"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    lead_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    touch_type: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    sent_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    lead: Mapped["Lead"] = relationship("Lead", back_populates="touches")

    def __repr__(self) -> str:
        """Return a concise debug representation.

        Returns:
            str: A representation including id, lead_id, and touch
                type.
        """
        return (
            f"<LeadTouch id={self.id} lead_id={self.lead_id} "
            f"touch_type={self.touch_type!r} status={self.status!r}>"
        )
