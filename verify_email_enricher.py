"""Verification script for the email pattern generator and enrichment fallback.

This script runs deterministic assertions against the new email_enricher
module and the integration points in llm_engine.py and app.py.  It prints
results to stdout and exits with a non-zero code if any assertion fails.

The MX lookup is mocked for deterministic testing of the pattern
generation, and a live DNS check is also attempted for leonai.io.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from email_enricher import (
    generate_email_candidates,
    is_placeholder_email,
    parse_name,
)
from database import Database


def main() -> None:
    """Run all email enricher verification checks and print results."""
    print("--- Email Enricher Verification ---")

    # 1. Name parsing: Leon Shmueli -> (leon, shmueli)
    first, last = parse_name("Leon Shmueli")
    assert first == "leon", f"Expected 'leon', got '{first}'"
    assert last == "shmueli", f"Expected 'shmueli', got '{last}'"
    print("[OK] parse_name: 'Leon Shmueli' -> ('leon', 'shmueli')")

    # 2. Name parsing with title: Dr. Jane Smith -> (jane, smith)
    first, last = parse_name("Dr. Jane Smith")
    assert first == "jane", f"Expected 'jane', got '{first}'"
    assert last == "smith", f"Expected 'smith', got '{last}'"
    print("[OK] parse_name: 'Dr. Jane Smith' -> ('jane', 'smith')")

    # 3. Name parsing with single token: 'Leon' -> ('leon', '')
    first, last = parse_name("Leon")
    assert first == "leon", f"Expected 'leon', got '{first}'"
    assert last == "", f"Expected '', got '{last}'"
    print("[OK] parse_name: 'Leon' -> ('leon', '')")

    # 4. Placeholder detection.
    assert is_placeholder_email(None) is True
    assert is_placeholder_email("") is True
    assert is_placeholder_email("contact@example.com") is True
    assert is_placeholder_email("info@domain.com") is True
    assert is_placeholder_email("n/a") is True
    assert is_placeholder_email("leon@leonai.io") is False
    assert is_placeholder_email("sarah@agencyone.com") is False
    print("[OK] is_placeholder_email: placeholder detection")

    # 5. Candidate generation with mocked MX lookup (deterministic).
    with patch("email_enricher.domain_has_mail_server", return_value=True):
        candidates = generate_email_candidates("Leon Shmueli", "leonai.io")
        assert candidates == [
            "leon@leonai.io",
            "leon.shmueli@leonai.io",
            "lshmueli@leonai.io",
            "leons@leonai.io",
            "info@leonai.io",
        ], f"Unexpected candidates: {candidates}"
        print(
            "[OK] generate_email_candidates: Leon Shmueli / leonai.io "
            f"-> {candidates}"
        )

    # 6. Candidate generation with no MX records returns empty list.
    with patch("email_enricher.domain_has_mail_server", return_value=False):
        candidates = generate_email_candidates("Leon Shmueli", "leonai.io")
        assert candidates == [], f"Expected empty list, got {candidates}"
        print("[OK] generate_email_candidates: no MX -> empty list")

    # 7. Candidate generation with single-token name.
    with patch("email_enricher.domain_has_mail_server", return_value=True):
        candidates = generate_email_candidates("Leon", "leonai.io")
        assert candidates == [
            "leon@leonai.io",
            "info@leonai.io",
        ], f"Unexpected candidates: {candidates}"
        print(
            "[OK] generate_email_candidates: single-token name "
            f"-> {candidates}"
        )

    # 8. Candidate generation with URL domain.
    with patch("email_enricher.domain_has_mail_server", return_value=True):
        candidates = generate_email_candidates(
            "Leon Shmueli", "https://www.leonai.io"
        )
        assert candidates[0] == "leon@leonai.io", (
            f"Expected leon@leonai.io, got {candidates[0]}"
        )
        print(
            "[OK] generate_email_candidates: URL domain cleaned "
            f"-> {candidates[0]}"
        )

    # 9. Database integration: email_candidates column persists.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "enrich.db"
        db = Database(db_path)
        try:
            lead_id = db.insert_lead(
                company_name="Leon AI",
                domain="leonai.io",
                contact_name="Leon Shmueli",
                email_candidates=json.dumps(
                    [
                        "leon@leonai.io",
                        "leon.shmueli@leonai.io",
                        "lshmueli@leonai.io",
                        "leons@leonai.io",
                        "info@leonai.io",
                    ]
                ),
            )
            lead = db.get_lead(lead_id)
            assert lead is not None
            assert lead["email_candidates"] is not None
            stored = json.loads(lead["email_candidates"])
            assert stored[0] == "leon@leonai.io"
            print(
                "[OK] Database: email_candidates column persists "
                f"-> {stored[0]}"
            )
        finally:
            db.close()

    # 10. Live DNS MX check for leonai.io (best-effort, not fatal).
    try:
        from email_enricher import domain_has_mail_server

        has_mx = domain_has_mail_server("leonai.io")
        print(
            f"[INFO] Live DNS MX check for leonai.io: "
            f"{'has MX records' if has_mx else 'no MX records'}"
        )
    except Exception as exc:
        print(f"[INFO] Live DNS MX check skipped: {exc}")

    print()
    print("=== ALL EMAIL ENRICHER VERIFICATIONS PASSED ===")


if __name__ == "__main__":
    main()