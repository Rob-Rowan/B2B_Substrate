"""Deterministic backend verification script for B2B Substrate.

This script runs assertions against the refactored SQLAlchemy ORM
layer, the manual ingestion/deduplication service, the status
lifecycle transition graph, and the Jinja2 draft-interpolation
engine.  It prints results to stdout and exits with a non-zero code
if any assertion fails.

CRITICAL: This script never runs against the production ``leads.db``
file. Every check below opens a fresh temporary SQLite database via
``tempfile`` so the existing ``leads`` and ``lead_touches`` data is
never touched, dropped, or altered.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from config import ALLOWED_TRANSITIONS, LEAD_STATES
from lead_service import (
    DuplicateLeadError,
    InvalidTransitionError,
    UnknownStatusError,
    create_lead,
    generate_lead_draft,
    list_leads,
    transition_lead_status,
)
from models import Base, Lead, LeadTouch
from templates_engine import extract_first_name, render_draft


def main() -> None:
    """Run all backend verification checks and print the results.

    Raises:
        AssertionError: If any deterministic backend assertion fails,
            causing the script to exit with a non-zero status code.
    """
    print("--- B2B Substrate Backend Verification ---")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "verify.db"
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(engine)
        session_local = sessionmaker(bind=engine, expire_on_commit=False)

        # 1. Manual ingestion: happy path -> QUALIFIED status.
        with session_local() as session:
            lead = create_lead(
                session,
                company_name="Acme Corp",
                contact_name="Jane Doe",
                website="https://www.acme.com/pricing",
                contact_title="CTO",
                email="Jane@Acme.com",
                tech_stack="Django, PostgreSQL",
                notes="Met at conference.",
            )
            session.commit()
            assert lead.status == "QUALIFIED"
            assert lead.domain == "acme.com"
            assert lead.verified_email == "jane@acme.com"
            print(
                "[OK] create_lead: normalizes website/email, defaults "
                "to QUALIFIED"
            )

        # 2. Deduplication: duplicate email -> DuplicateLeadError (409).
        with session_local() as session:
            try:
                create_lead(
                    session,
                    company_name="Acme Clone",
                    email="jane@acme.com",
                )
                raise AssertionError("Expected DuplicateLeadError")
            except DuplicateLeadError as exc:
                payload = exc.payload.to_dict()
                assert payload["status_code"] == 409
                assert payload["field"] == "email"
            print(
                "[OK] create_lead: duplicate email -> 409 "
                "DuplicateLeadError"
            )

        # 3. Deduplication: duplicate website -> DuplicateLeadError (409).
        with session_local() as session:
            try:
                create_lead(
                    session,
                    company_name="Acme Clone 2",
                    website="acme.com",
                )
                raise AssertionError("Expected DuplicateLeadError")
            except DuplicateLeadError as exc:
                payload = exc.payload.to_dict()
                assert payload["status_code"] == 409
                assert payload["field"] == "website"
            print(
                "[OK] create_lead: duplicate website -> 409 "
                "DuplicateLeadError"
            )

        # 4. Status lifecycle: legal transition QUALIFIED -> QUEUED.
        with session_local() as session:
            leads = list_leads(session, search_term="Acme Corp")
            lead_id = leads[0].id
            updated = transition_lead_status(session, lead_id, "QUEUED")
            session.commit()
            assert updated.status == "QUEUED"
            print("[OK] transition_lead_status: QUALIFIED -> QUEUED allowed")

        # 5. Status lifecycle: illegal transition QUEUED -> REPLIED rejected.
        with session_local() as session:
            try:
                transition_lead_status(session, lead_id, "REPLIED")
                raise AssertionError("Expected InvalidTransitionError")
            except InvalidTransitionError as exc:
                assert exc.payload.status_code == 400
            print(
                "[OK] transition_lead_status: QUEUED -> REPLIED "
                "rejected (400)"
            )

        # 6. Status lifecycle: unknown status rejected.
        with session_local() as session:
            try:
                transition_lead_status(session, lead_id, "UNPROCESSED")
                raise AssertionError("Expected UnknownStatusError")
            except UnknownStatusError as exc:
                assert exc.payload.status_code == 400
            print(
                "[OK] transition_lead_status: UNPROCESSED rejected as "
                "unknown status"
            )

        # 7. LEAD_STATES contains exactly the six required states, no
        #    UNPROCESSED anywhere.
        assert set(LEAD_STATES) == {
            "QUALIFIED",
            "QUEUED",
            "SENT",
            "REPLIED",
            "DISQUALIFIED",
            "ARCHIVED",
        }
        assert "UNPROCESSED" not in LEAD_STATES
        for targets in ALLOWED_TRANSITIONS.values():
            assert "UNPROCESSED" not in targets
        print(
            "[OK] LEAD_STATES: exactly 6 states, UNPROCESSED absent "
            "everywhere"
        )

        # 8. Draft interpolation: first-name extraction.
        assert extract_first_name("Leon Shmueli") == "Leon"
        assert extract_first_name(None) == "there"
        assert extract_first_name("") == "there"
        assert extract_first_name("madonna") == "Madonna"
        print("[OK] extract_first_name: parses first token, safe fallback")

        # 9. Draft interpolation: template renders tech_stack + first name.
        draft = render_draft(
            company_name="Leon AI",
            contact_name="Leon Shmueli",
            tech_stack="FastAPI, Postgres",
        )
        assert "Leon" in draft.body
        assert "FastAPI, Postgres" in draft.body
        assert "Leon AI" in draft.subject
        print(
            "[OK] render_draft: interpolates first_name + tech_stack "
            "into draft"
        )

        # 10. generate_lead_draft persists subject/body onto the lead row.
        with session_local() as session:
            rendered = generate_lead_draft(session, lead_id)
            session.commit()
        with session_local() as session:
            persisted = session.get(Lead, lead_id)
            assert persisted is not None
            assert persisted.custom_subject == rendered.subject
            assert persisted.custom_pitch == rendered.body
        print(
            "[OK] generate_lead_draft: persists rendered subject/body "
            "onto the lead"
        )

        # 11. Lead.touches relationship + cascade delete-orphan.
        with session_local() as session:
            touch_lead = create_lead(session, company_name="Touch Co")
            session.flush()
            touch = LeadTouch(
                lead_id=touch_lead.id,
                touch_type="EMAIL",
                subject="Hello",
                body="Body text",
                status="DRAFT",
                created_at="2026-01-01T00:00:00",
            )
            touch_lead.touches.append(touch)
            session.commit()
            touch_lead_id = touch_lead.id

        with session_local() as session:
            reloaded = session.get(Lead, touch_lead_id)
            assert reloaded is not None
            assert len(reloaded.touches) == 1
            assert reloaded.touches[0].lead is reloaded
            session.delete(reloaded)
            session.commit()

        with session_local() as session:
            orphans = (
                session.execute(
                    select(LeadTouch).where(
                        LeadTouch.lead_id == touch_lead_id
                    )
                )
                .scalars()
                .all()
            )
            assert orphans == []
        print(
            "[OK] Lead.touches <-> LeadTouch.lead: relationship + "
            "cascade delete-orphan works"
        )

        engine.dispose()

    print()
    print("=== ALL BACKEND VERIFICATIONS PASSED ===")
    print(
        "NOTE: All checks ran against an isolated temporary SQLite "
        "database. The production leads.db was never opened by this "
        "script."
    )


if __name__ == "__main__":
    main()
