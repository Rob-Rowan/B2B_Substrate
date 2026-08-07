"""Edge-case tests for flexible field aliases and URL parsing in ingestion.py.

With non-blocking partial lead ingestion, the only strictly required
field is ``company_name``.  Leads missing ``domain`` or ``email`` are
inserted into the database rather than skipped, and the LLM is
instructed to generate ``search_helpers`` to locate the missing data.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from database import Database
from ingestion import IngestionPipeline
from verifier import EmailVerifier


def make_pipeline() -> IngestionPipeline:
    """Create a pipeline with a temp DB and MX-check disabled for determinism."""
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    db_path = Path(tmp.name) / "test.db"
    db = Database(db_path)
    # Disable MX requirement so tests don't depend on live DNS resolution.
    verifier = EmailVerifier(require_mx=False)
    pipeline = IngestionPipeline(db, verifier=verifier)
    return pipeline


def main() -> None:
    passed = 0

    # --- Test 1: URL parsing with https:// and www. stripping ---
    pipeline = make_pipeline()
    result = pipeline.ingest_single_lead({
        "name": "Test Co",
        "websiteUrl": "https://www.testco.com",
        "email": "sarah@testco.com",
    })
    assert result.domain == "testco.com", (
        f"Expected testco.com, got {result.domain}"
    )
    assert result.inserted is True, (
        f"Expected inserted=True, got skipped={result.skipped}, "
        f"reason={result.reason}"
    )
    print(f"[OK] Test 1: HTTPS + www. stripped -> '{result.domain}'")
    passed += 1

    # --- Test 2: URL parsing with http:// (no www) ---
    pipeline = make_pipeline()
    result = pipeline.ingest_single_lead({
        "company": "Test Co 2",
        "url": "http://testco2.com",
        "publicEmail": "john@testco2.com",
    })
    assert result.domain == "testco2.com", (
        f"Expected testco2.com, got {result.domain}"
    )
    assert result.inserted is True
    print(f"[OK] Test 2: HTTP URL (no www) -> '{result.domain}'")
    passed += 1

    # --- Test 3: URL with www. + https, using partner_name alias ---
    pipeline = make_pipeline()
    result = pipeline.ingest_single_lead({
        "partnerName": "Partner Co",
        "website": "https://www.partnerco.com",
        "contactEmail": "maria@partnerco.com",
    })
    assert result.domain == "partnerco.com", (
        f"Expected partnerco.com, got {result.domain}"
    )
    assert result.company_name == "Partner Co"
    assert result.email == "maria@partnerco.com"
    assert result.inserted is True
    print(f"[OK] Test 3: WWW + HTTPS stripped -> '{result.domain}'")
    passed += 1

    # --- Test 4: Plain domain (no http prefix) ---
    pipeline = make_pipeline()
    result = pipeline.ingest_single_lead({
        "title": "Plain Domain Co",
        "website_url": "plaindomain.com",
        "verified_email": "sarah@plaindomain.com",
    })
    assert result.domain == "plaindomain.com", (
        f"Expected plaindomain.com, got {result.domain}"
    )
    assert result.inserted is True
    print(f"[OK] Test 4: Plain domain unchanged -> '{result.domain}'")
    passed += 1

    # --- Test 5: Missing email field (now inserted, not skipped) ---
    pipeline = make_pipeline()
    result = pipeline.ingest_single_lead({
        "company_name": "No Email Co",
        "domain": "noemail.com",
    })
    assert result.inserted is True, f"Expected inserted=True, got {result.reason}"
    assert result.email is None
    assert result.domain == "noemail.com"
    print("[OK] Test 5: Missing email -> inserted with email=None")
    passed += 1

    # --- Test 6: Missing company_name (only strictly required field) ---
    pipeline = make_pipeline()
    result = pipeline.ingest_single_lead({
        "domain": "nocname.com",
        "email": "sarah@nocname.com",
    })
    assert result.skipped is True
    assert result.inserted is False
    assert "company_name" in result.reason.lower(), f"Reason: {result.reason}"
    print(f"[OK] Test 6: Missing company_name -> '{result.reason}'")
    passed += 1

    # --- Test 7: Missing domain field (now inserted, not skipped) ---
    pipeline = make_pipeline()
    result = pipeline.ingest_single_lead({
        "company_name": "No Domain Co",
        "email": "sarah@nodomain.com",
    })
    assert result.inserted is True, f"Expected inserted=True, got {result.reason}"
    assert result.domain is None
    assert result.email == "sarah@nodomain.com"
    print("[OK] Test 7: Missing domain -> inserted with domain=None")
    passed += 1

    # --- Test 8: All fields missing (company_name required) ---
    pipeline = make_pipeline()
    result = pipeline.ingest_single_lead({})
    assert result.skipped is True
    assert result.inserted is False
    assert "company_name" in result.reason.lower(), f"Reason: {result.reason}"
    print(f"[OK] Test 8: All missing -> '{result.reason}'")
    passed += 1

    # --- Test 9: Field alias priority (first match wins in chain order) ---
    pipeline = make_pipeline()
    # company_name takes priority over name
    result = pipeline.ingest_single_lead({
        "company_name": "Primary Co",
        "name": "Secondary Co",
        "domain": "primaryco.com",
        "email": "sarah@primaryco.com",
    })
    assert result.company_name == "Primary Co", f"Got: {result.company_name}"
    assert result.inserted is True
    print(f"[OK] Test 9a: company_name beats name -> '{result.company_name}'")
    passed += 1

    # name takes priority over company (name is earlier in the fallback chain)
    pipeline = make_pipeline()
    result = pipeline.ingest_single_lead({
        "name": "Name Based Co",
        "company": "Company Co",
        "domain": "namebasedco.com",
        "email": "sarah@namebasedco.com",
    })
    assert result.company_name == "Name Based Co", f"Got: {result.company_name}"
    print(f"[OK] Test 9b: name beats company -> '{result.company_name}'")
    passed += 1

    # --- Test 10: email alias priority (verified_email before email) ---
    pipeline = make_pipeline()
    result = pipeline.ingest_single_lead({
        "company_name": "Email Alias Co",
        "domain": "emailalias.com",
        "verified_email": "verified@emailalias.com",
        "email": "fallback@emailalias.com",
    })
    assert result.email == "verified@emailalias.com", f"Got: {result.email}"
    assert result.inserted is True
    print(f"[OK] Test 10: verified_email takes priority -> '{result.email}'")
    passed += 1

    # --- Test 11: Deduplication still retained ---
    pipeline = make_pipeline()
    first = pipeline.ingest_single_lead({
        "company_name": "Ag One",
        "domain": "agone.com",
        "verified_email": "sarah@agone.com",
    })
    assert first.inserted is True
    dup = pipeline.ingest_single_lead({
        "company_name": "Ag One Dup",
        "domain": "agone.com",
        "verified_email": "other@agone.com",
    })
    assert dup.skipped is True
    assert "already exists" in dup.reason
    print(f"[OK] Test 11: Deduplication retained -> '{dup.reason}'")
    passed += 1

    # --- Test 12: Partial lead with only company_name (no domain, no email) ---
    pipeline = make_pipeline()
    result = pipeline.ingest_single_lead({
        "company_name": "Only Name Co",
        "description": "A small dev shop that builds web apps.",
        "tiers": "Silver",
    })
    assert result.inserted is True, f"Expected inserted=True, got {result.reason}"
    assert result.domain is None
    assert result.email is None
    print(f"[OK] Test 12: company_name only -> '{result.reason}'")
    passed += 1

    print()
    print(f"=== ALL {passed} EDGE-CASE TESTS PASSED ===")


if __name__ == "__main__":
    main()
