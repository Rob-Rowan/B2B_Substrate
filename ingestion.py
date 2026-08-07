"""Ingestion and sanitization pipeline for B2B Substrate.

This module implements the data ingestion layer that fetches raw JSON
endpoints and directory inputs over HTTP, sanitizes all web context
before it reaches the LLM engine or the database, and enforces strict
database deduplication so that domains and emails already present in
``leads.db`` are silently skipped.

The module exposes an :class:`IngestionPipeline` class that orchestrates
the full flow: fetch → sanitize → verify → deduplicate → insert.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from config import INGESTION_MAX_BYTES, INGESTION_TIMEOUT
from database import Database
from sanitizer import sanitize_text
from verifier import EmailVerifier, VerificationResult

# ---------------------------------------------------------------------------
# Ingestion result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestionResult:
    """Result of a single lead ingestion attempt.

    Attributes:
        company_name: The company name that was ingested.
        domain: The domain that was ingested, or ``None`` when not
            available in the source data.
        email: The verified email that was ingested, or ``None`` when
            not available in the source data.
        inserted: ``True`` when the lead was inserted into the database.
        skipped: ``True`` when the lead was skipped due to a duplicate,
            missing required field, or verification failure.
        reason: A human-readable explanation of the outcome.
    """

    company_name: str
    domain: str | None = None
    email: str | None = None
    inserted: bool = False
    skipped: bool = False
    reason: str = ""


@dataclass
class IngestionSummary:
    """Summary of a batch ingestion run.

    Attributes:
        results: The list of per-lead ingestion results.
        inserted_count: Number of leads successfully inserted.
        skipped_count: Number of leads skipped.
        failed_count: Number of leads that raised an error.
    """

    results: list[IngestionResult] = field(default_factory=list)
    inserted_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0

    def add_result(self, result: IngestionResult) -> None:
        """Record a single ingestion result and update the counters.

        Args:
            result: The ingestion result to record.
        """
        self.results.append(result)
        if result.inserted:
            self.inserted_count += 1
        elif result.skipped:
            self.skipped_count += 1
        else:
            self.failed_count += 1


# ---------------------------------------------------------------------------
# Ingestion pipeline
# ---------------------------------------------------------------------------


class IngestionPipeline:
    """HTTP ingestion and sanitization pipeline for B2B leads.

    The pipeline fetches raw JSON from endpoints or directory inputs,
    sanitizes all web context, verifies email deliverability, enforces
    database deduplication, and inserts new leads into the database.

    Attributes:
        db: The active database connection.
        verifier: The email verification firewall.
        timeout: HTTP request timeout in seconds.
        max_bytes: Maximum number of bytes accepted from a single source.
    """

    def __init__(
        self,
        db: Database,
        *,
        verifier: EmailVerifier | None = None,
        timeout: float = INGESTION_TIMEOUT,
        max_bytes: int = INGESTION_MAX_BYTES,
    ) -> None:
        """Initialize the ingestion pipeline.

        Args:
            db: The active database connection.
            verifier: The email verification firewall.  When ``None``, a
                default :class:`EmailVerifier` is created.
            timeout: HTTP request timeout in seconds.
            max_bytes: Maximum number of bytes accepted from a single
                source.
        """
        self.db: Database = db
        self.verifier: EmailVerifier = verifier or EmailVerifier()
        self.timeout: float = timeout
        self.max_bytes: int = max_bytes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest_json_url(self, url: str) -> IngestionSummary:
        """Ingest leads from a raw JSON endpoint.

        The endpoint is expected to return either a JSON array of lead
        objects or a JSON object wrapping an array under a known key
        (e.g. ``leads``, ``data``, ``results``, ``items``, or
        ``partners``).

        Args:
            url: The URL of the JSON endpoint.

        Returns:
            IngestionSummary: The aggregated ingestion results.

        Raises:
            httpx.HTTPError: If the HTTP request fails.
            ValueError: If the response is not valid JSON or exceeds the
                maximum byte limit.
        """
        response = httpx.get(url, timeout=self.timeout, follow_redirects=True)
        response.raise_for_status()

        content = response.content
        if len(content) > self.max_bytes:
            raise ValueError(
                f"Response from {url} exceeds the maximum size of "
                f"{self.max_bytes} bytes."
            )

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Response from {url} is not valid JSON: {exc}"
            ) from exc

        leads = self._normalize_lead_list(data)
        return self.ingest_lead_list(leads)

    def ingest_json_bytes(self, content: bytes) -> IngestionSummary:
        """Ingest leads from raw JSON bytes.

        Accepts the raw bytes of a JSON file dumped from DevTools or a
        scraping tool.  The payload may be a flat array of lead objects
        or a nested dictionary wrapper.

        Args:
            content: The raw JSON bytes to parse.

        Returns:
            IngestionSummary: The aggregated ingestion results.

        Raises:
            ValueError: If the payload is not valid JSON or exceeds the
                maximum byte limit.
        """
        if len(content) > self.max_bytes:
            raise ValueError(
                f"Uploaded payload exceeds the maximum size of "
                f"{self.max_bytes} bytes."
            )

        try:
            data = json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(
                f"Uploaded payload is not valid JSON: {exc}"
            ) from exc

        leads = self._normalize_lead_list(data)
        return self.ingest_lead_list(leads)

    def ingest_json_text(self, text: str) -> IngestionSummary:
        """Ingest leads from a raw JSON string.

        Accepts raw JSON text pasted directly into the UI.  The payload
        may be a flat array of lead objects or a nested dictionary
        wrapper.

        Args:
            text: The raw JSON string to parse.

        Returns:
            IngestionSummary: The aggregated ingestion results.

        Raises:
            ValueError: If the payload is not valid JSON or exceeds the
                maximum byte limit.
        """
        content = text.encode("utf-8")
        if len(content) > self.max_bytes:
            raise ValueError(
                f"Pasted payload exceeds the maximum size of "
                f"{self.max_bytes} bytes."
            )

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Pasted payload is not valid JSON: {exc}"
            ) from exc

        leads = self._normalize_lead_list(data)
        return self.ingest_lead_list(leads)

    def ingest_lead_list(
        self, leads: list[dict[str, Any]]
    ) -> IngestionSummary:
        """Ingest a list of lead dictionaries.

        Each lead dictionary may contain the following keys:

        * ``company_name`` (required — the only strictly required field)
        * ``domain`` (optional — used for deduplication and LLM context)
        * ``email`` or ``verified_email`` (optional — used for email
          outreach; when absent the lead is still ingested)
        * ``contact_name`` (optional)
        * ``title`` (optional)
        * ``slug`` (optional — folded into ``website_text``)
        * ``description`` (optional — folded into ``website_text``)
        * ``address`` (optional — folded into ``website_text``)
        * ``tiers`` (optional — folded into ``website_text``)
        * ``website_text`` (optional — raw scraped content)
        * ``notes`` (optional)

        When ``domain`` or ``email`` are missing, the remaining metadata
        fields (``description``, ``address``, ``tiers``, ``slug``) are
        combined into ``website_text`` so the LLM still has context.
        Deduplication and email verification only run when the
        corresponding field is present.

        Args:
            leads: The list of lead dictionaries to ingest.

        Returns:
            IngestionSummary: The summary of all ingestion results.
        """
        summary = IngestionSummary()

        for lead in leads:
            result = self._ingest_single_lead(lead)
            summary.add_result(result)

        return summary

    def ingest_single_lead(self, lead: dict[str, Any]) -> IngestionResult:
        """Ingest a single lead dictionary.

        Args:
            lead: The lead dictionary to ingest.

        Returns:
            IngestionResult: The result of the ingestion attempt.
        """
        return self._ingest_single_lead(lead)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalize_lead_list(self, data: Any) -> list[dict[str, Any]]:
        """Normalize raw JSON data into a list of lead dictionaries.

        Handles both flat JSON arrays (``[{...}, {...}]``) and nested
        dictionary wrappers (e.g. ``{"data": [...]}`` or
        ``{"partners": [...]}``).  When a wrapper key is found, the
        value must be a list; otherwise the key is skipped and the next
        candidate is tried.

        Args:
            data: The raw JSON data from the endpoint, file, or paste.

        Returns:
            list[dict[str, Any]]: A list of lead dictionaries.

        Raises:
            ValueError: If the data is not a list or a dict containing a
                list under a recognized wrapper key.
        """
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]

        if isinstance(data, dict):
            for key in ("leads", "data", "results", "items", "partners"):
                if key in data and isinstance(data[key], list):
                    return [
                        item for item in data[key] if isinstance(item, dict)
                    ]

        raise ValueError(
            "JSON payload must be an array of lead objects or an object "
            "with a 'leads', 'data', 'results', 'items', or 'partners' "
            "array."
        )

    def _ingest_single_lead(self, lead: dict[str, Any]) -> IngestionResult:
        """Process a single lead through the full pipeline.

        Args:
            lead: The lead dictionary to process.

        Returns:
            IngestionResult: The result of the ingestion attempt.
        """
        # Resolve company_name from multiple fallback keys.
        company_name = ""
        for key in (
            "company_name",
            "name",
            "title",
            "partner_name",
            "company",
            "partnerName",
        ):
            value = lead.get(key)
            if value:
                company_name = str(value).strip()
                break

        # Resolve domain from multiple fallback keys, parsing URLs if
        # the value is a full http(s) URL.
        domain = ""
        for key in ("domain", "websiteUrl", "website", "url", "website_url"):
            value = lead.get(key)
            if value:
                domain = str(value).strip().lower()
                break
        if domain.startswith(("http://", "https://")):
            parsed = urlparse(domain)
            domain = parsed.netloc
            if domain.startswith("www."):
                domain = domain[4:]

        # Resolve email from multiple fallback keys.
        email = ""
        for key in (
            "verified_email",
            "email",
            "contactEmail",
            "contact_email",
            "publicEmail",
        ):
            value = lead.get(key)
            if value:
                email = str(value).strip().lower()
                break

        # Only company_name is strictly required.  If it is missing
        # there is nothing to ingest and the lead is skipped.
        if not company_name:
            return IngestionResult(
                company_name="",
                domain=None,
                email=None,
                inserted=False,
                skipped=True,
                reason="Missing company_name field in source JSON.",
            )

        # Normalize empty strings to None for the optional fields so
        # they can be persisted cleanly in the nullable database columns.
        domain = domain or None
        email = email or None

        # Deduplication check: silently skip if a non-null domain or email
        # already exists in the database.  Only checked when the value is
        # present so partial leads (no domain/email) can still be ingested.
        if domain and self.db.get_lead_by_domain(domain) is not None:
            return IngestionResult(
                company_name=company_name,
                domain=domain,
                email=email,
                inserted=False,
                skipped=True,
                reason="Domain already exists in the database.",
            )

        if email and self.db.get_lead_by_email(email) is not None:
            return IngestionResult(
                company_name=company_name,
                domain=domain,
                email=email,
                inserted=False,
                skipped=True,
                reason="Email already exists in the database.",
            )

        # Email verification firewall.  Only run when an email is present
        # — leads without an email are still ingested so the user can
        # supply one during triage.
        if email:
            verification: VerificationResult = self.verifier.verify(email)
            if not verification.is_valid:
                return IngestionResult(
                    company_name=company_name,
                    domain=domain,
                    email=email,
                    inserted=False,
                    skipped=True,
                    reason=verification.reason,
                )

        # Build website_text from raw scraped text and extra metadata
        # fields.  When no domain is available, this gives the LLM
        # context (description, address, tiers, slug) to evaluate the
        # lead even without a scraped domain.
        website_text_parts: list[str] = []

        raw_website_text = lead.get("website_text")
        if raw_website_text:
            website_text_parts.append(str(raw_website_text))

        for meta_key in ("description", "address", "tiers", "slug"):
            meta_value = lead.get(meta_key)
            if meta_value:
                if isinstance(meta_value, (list, dict)):
                    website_text_parts.append(
                        f"{meta_key}: {json.dumps(meta_value, ensure_ascii=False)}"
                    )
                else:
                    website_text_parts.append(
                        f"{meta_key}: {str(meta_value)}"
                    )

        website_text = (
            "\n\n".join(website_text_parts) if website_text_parts else None
        )
        sanitized_text = sanitize_text(website_text) if website_text else None

        # Resolve contact_name, title, and notes from the lead dict.
        contact_name = (
            str(lead["contact_name"]).strip()
            if lead.get("contact_name")
            else None
        )
        title = (
            str(lead["title"]).strip() if lead.get("title") else None
        )
        notes = (
            str(lead["notes"]).strip() if lead.get("notes") else None
        )

        try:
            lead_id = self.db.insert_lead(
                company_name=company_name,
                domain=domain,
                verified_email=email,
                contact_name=contact_name,
                title=title,
                website_text=website_text,
                sanitized_text=sanitized_text,
                notes=notes,
            )
        except Exception as exc:
            return IngestionResult(
                company_name=company_name,
                domain=domain,
                email=email,
                inserted=False,
                skipped=False,
                reason=f"Database insert failed: {exc}",
            )

        return IngestionResult(
            company_name=company_name,
            domain=domain,
            email=email,
            inserted=True,
            skipped=False,
            reason=f"Lead inserted with ID {lead_id}.",
        )
