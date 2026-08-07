"""Phase 2 integration verification script for B2B Substrate.

This script runs deterministic assertions against the Phase 2 modules
(database, sanitizer, verifier, emailer, ingestion, llm_engine) and
prints the results to stdout.  It exits with a non-zero code if any
assertion fails.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from database import Database
from sanitizer import sanitize_text
from verifier import EmailVerifier, is_disposable_domain
from emailer import (
    build_email_1_body,
    build_followup_1_body,
    build_breakup_body,
)


def main() -> None:
    """Run all Phase 2 verification checks and print the results."""
    print("--- Phase 2 Verification ---")

    # 1. Database: state machine + rolling send window count.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "t.db"
        db = Database(db_path)
        try:
            lead_id = db.insert_lead(
                company_name="Q Agency",
                domain="qagency.com",
                verified_email="dev@qagency.com",
                tech_stack="Django, React",
            )
            db.mark_qualified(lead_id)
            db.mark_email_1_sent(lead_id)
            lead = db.get_lead(lead_id)
            assert lead is not None and lead["status"] == "EMAIL_1_SENT"
            assert lead["followup_1_due_date"] is not None
            assert db.get_sent_in_window_count(24) >= 1
            print("[OK] Database: state machine + rolling window count")
        finally:
            db.close()

    # 2. Sanitizer: HTML stripping + entity decoding.
    entity_amp = "&" + "amp;"
    cleaned = sanitize_text(
        f"<script>x</script><p>Hi {entity_amp} welcome</p>"
    )
    assert "script" not in cleaned
    assert "amp;" not in cleaned
    print("[OK] Sanitizer: HTML stripping + entity decode")

    # 3. Verifier: disposable domain block + role-based rejection.
    assert is_disposable_domain("mailinator.com")
    verifier = EmailVerifier(require_mx=False)
    valid = verifier.verify("john@qagency.com")
    assert valid.is_valid
    invalid = verifier.verify("info@mailinator.com")
    assert not invalid.is_valid
    print("[OK] Verifier: disposable + role-based rejection")

    # 4. Email builders: signature no longer contains GitHub/LinkedIn URLs.
    body = build_email_1_body(
        "Pitch sentence one. Pitch sentence two. Pitch sentence three."
    )
    assert "github.com" not in body
    assert "linkedin.com" not in body
    assert "Rob Rowan" in body
    assert build_followup_1_body("X")
    assert build_breakup_body("X")
    print(
        "[OK] Email builders: signature w/o URLs "
        "+ follow-up/breakup templates"
    )

    # 5. LLM Engine: credential resolution + Pydantic schema.
    from config import load_config
    from llm_engine import LLMEngine, LeadEvaluation

    app_config = load_config()
    assert app_config.credentials.has_credentials is True
    engine = LLMEngine(credentials=app_config.credentials)
    assert engine.credentials.gemini_model == "gemini-3.6-flash"
    schema = LeadEvaluation.model_json_schema()
    assert set(schema["required"]) == {
        "qualification_verdict",
        "reasoning",
        "custom_pitch",
    }
    # search_helpers is optional (has default), so it must NOT be required
    # but must be present as a property.
    assert "search_helpers" in schema["properties"]
    assert "search_helpers" not in schema["required"]
    print("[OK] LLM Engine: credentials + Pydantic schema (search_helpers optional)")

    # 6. Meeting Booked state: LEAD_STATES + count helper.
    from config import LEAD_STATES

    assert "MEETING_BOOKED" in LEAD_STATES
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_m:
        dbm_path = Path(tmp_m) / "m.db"
        dbm = Database(dbm_path)
        try:
            mid = dbm.insert_lead(
                company_name="M Agency",
                domain="magency.com",
                verified_email="m@magency.com",
            )
            dbm.mark_meeting_booked(mid)
            assert dbm.get_lead(mid)["status"] == "MEETING_BOOKED"
            assert dbm.get_meetings_booked_count() == 1
            print("[OK] Meeting Booked: state + KPI count")
        finally:
            dbm.close()

    # 7. Ingestion: deduplication + verification filtering.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp2:
        db2_path = Path(tmp2) / "ingest.db"
        db2 = Database(db2_path)
        try:
            from ingestion import IngestionPipeline

            pipeline = IngestionPipeline(db2)
            result = pipeline.ingest_single_lead(
                {
                    "company_name": "Agency One",
                    "domain": "agencyone.com",
                    "verified_email": "sarah@agencyone.com",
                    "website_text": "<p>We build web apps.</p>",
                }
            )
            assert result.inserted is True

            duplicate = pipeline.ingest_single_lead(
                {
                    "company_name": "Agency Dup",
                    "domain": "agencyone.com",
                    "verified_email": "other@agencyone.com",
                }
            )
            assert duplicate.skipped is True
            assert "already exists" in duplicate.reason

            role_based = pipeline.ingest_single_lead(
                {
                    "company_name": "Agency Three",
                    "domain": "agencythree.com",
                    "verified_email": "info@agencythree.com",
                }
            )
            assert role_based.skipped is True
            assert "role-based" in role_based.reason.lower()
            print("[OK] Ingestion: dedup + verification filtering")
        finally:
            db2.close()

    # 8. Partial ingestion: only company_name required (no domain/email).
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp3:
        db3_path = Path(tmp3) / "partial.db"
        db3 = Database(db3_path)
        try:
            pipeline3 = IngestionPipeline(db3)
            partial = pipeline3.ingest_single_lead(
                {
                    "company_name": "Partial Lead Co",
                    "description": "Builds mobile apps with backend API.",
                    "address": "San Francisco, CA",
                    "tiers": "Silver",
                }
            )
            assert partial.inserted is True
            assert partial.domain is None
            assert partial.email is None
            # The lead should be retrievable by company name search.
            leads = db3.list_leads(search_term="Partial Lead Co")
            assert len(leads) == 1
            # website_text should contain the metadata we passed in.
            stored = leads[0]
            assert stored["website_text"] is not None
            assert "mobile" in stored["website_text"].lower()
            print(
                "[OK] Partial ingestion: company_name only -> inserted "
                "with website_text from metadata"
            )
        finally:
            db3.close()

    print()
    print("=== ALL PHASE 2 VERIFICATIONS PASSED ===")


if __name__ == "__main__":
    main()
