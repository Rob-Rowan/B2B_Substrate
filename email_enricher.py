"""Automated email pattern generation and enrichment fallback for B2B Substrate.

This module implements the deterministic email pattern generator used to
enrich leads that arrive without a verified contact email.  Given a
contact name and a company domain, it produces the common corporate
email permutations:

* ``{first}@{domain}`` (highest probability)
* ``{first}.{last}@{domain}``
* ``{first_initial}{last}@{domain}``
* ``{first}{last_initial}@{domain}``
* ``info@{domain}`` (lowest probability fallback)

Before any candidates are returned, the domain is checked for active mail
servers via a lightweight DNS MX lookup (``dns.resolver.resolve(domain,
"MX")``).  When the domain has no MX records, the domain cannot receive
email and an empty list is returned so callers never enrich a lead toward
an undeliverable address.

The module exposes:

* :func:`generate_email_candidates` — the primary enrichment entry point.
* :func:`is_placeholder_email` — detects missing/placeholder emails.
* :func:`parse_name` — clean first/last name extraction.
* :func:`domain_has_mail_server` — MX verification used before returning.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from verifier import lookup_mx_records

# ---------------------------------------------------------------------------
# Placeholder detection
# ---------------------------------------------------------------------------

# Matches common placeholder addresses such as contact@example.com,
# info@domain.com, hello@test.org, etc.
_PLACEHOLDER_LOCAL_PATTERNS: Final[re.Pattern[str]] = re.compile(
    r"^(contact|info|mail|email|hello|test|user|admin|your|name)@"
    r"(example|domain|test|yourdomain|company|email|sample)\.(com|org|net|io|co)$",
    re.IGNORECASE,
)

# Bare placeholder literals that indicate "no real email on record".
_PLACEHOLDER_LITERALS: Final[tuple[str, ...]] = (
    "",
    "n/a",
    "na",
    "none",
    "unknown",
    "tbd",
    "to be determined",
    "todo",
    "placeholder",
    "missing",
    "not available",
    "unavailable",
    "@",
    "example.com",
    "example.org",
    "example.net",
)

# Common name titles stripped before first/last name extraction.
_COMMON_TITLES: Final[frozenset[str]] = frozenset(
    {
        "dr",
        "mr",
        "mrs",
        "ms",
        "miss",
        "mx",
        "prof",
        "rev",
        "sir",
        "madam",
        "md",
        "phd",
    }
)


def is_placeholder_email(email: str | None) -> bool:
    """Return ``True`` when an email is missing or a known placeholder.

    A lead is considered "without a usable email" when the value is empty,
    a bare literal such as ``"n/a"``, or a placeholder pattern such as
    ``contact@example.com``.

    Args:
        email: The stored ``verified_email`` value, or ``None``.

    Returns:
        bool: ``True`` when the value should be replaced by enrichment.
    """
    if not email:
        return True
    stripped = email.strip()
    lowered = stripped.lower()
    if lowered in _PLACEHOLDER_LITERALS:
        return True
    return _PLACEHOLDER_LOCAL_PATTERNS.match(stripped) is not None


# ---------------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------------


def parse_name(contact_name: str) -> tuple[str, str]:
    """Parse a contact name into normalized ``(first_name, last_name)``.

    The name is ASCII-folded (e.g. ``José`` → ``jose``), lowercased, and
    split on whitespace or commas.  Common titles (``Dr.``, ``Mr.``,
    ``Ms.``, ``Prof.``) are stripped before the first and last tokens are
    selected.  For a single-token name the last name is ``""``.

    Args:
        contact_name: The raw contact name, e.g. ``"Leon Shmueli"``.

    Returns:
        tuple[str, str]: The normalized first and last names.  Both are
            lowercased; the last name is ``""`` for single-token names.
    """
    if not contact_name:
        return "", ""

    normalized = unicodedata.normalize("NFKD", contact_name)
    ascii_name = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )

    tokens = [
        token
        for token in re.split(r"[\s,]+", ascii_name.strip().lower())
        if token
    ]
    if not tokens:
        return "", ""

    # Strip trailing periods from tokens (e.g. "dr." -> "dr") so common
    # titles are recognized regardless of punctuation.
    normalized_tokens = [token.rstrip(".") for token in tokens]

    filtered = [
        token
        for token in normalized_tokens
        if token not in _COMMON_TITLES
    ]
    if not filtered:
        filtered = normalized_tokens[-1:]

    first = filtered[0]
    last = filtered[-1] if len(filtered) >= 2 else ""
    return first, last


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------


def _clean_domain(domain: str) -> str:
    """Normalize a domain for candidate generation.

    Strips ``http(s)://`` prefixes, URL paths/query fragments, and a
    leading ``www.`` so generated addresses always use the bare domain.

    Args:
        domain: The raw domain value from the lead.

    Returns:
        str: The cleaned, lowercased domain with no trailing dot.
    """
    cleaned = domain.strip().lower()
    if "://" in cleaned:
        cleaned = cleaned.split("://", 1)[1]
    cleaned = cleaned.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned.rstrip(".")


def domain_has_mail_server(domain: str) -> bool:
    """Return ``True`` when the domain has active MX mail servers.

    The lookup delegates to :func:`verifier.lookup_mx_records`, which uses
    ``dns.resolver.resolve(domain, "MX")`` from ``dnspython`` (with a
    subprocess fallback when the package is unavailable).  Any lookup
    failure is treated as "no mail server" so enrichment never proposes
    addresses on a domain that cannot receive email.

    Args:
        domain: The bare domain name to check, e.g. ``leonai.io``.

    Returns:
        bool: ``True`` when at least one MX record was found.
    """
    try:
        return bool(lookup_mx_records(domain))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------


def generate_email_candidates(contact_name: str, domain: str) -> list[str]:
    """Generate the common corporate email permutations for a lead.

    The candidates are returned in descending probability order:

    1. ``{first}@{domain}``
    2. ``{first}.{last}@{domain}``
    3. ``{first_initial}{last}@{domain}``
    4. ``{first}{last_initial}@{domain}``
    5. ``info@{domain}``

    A lead with the name ``"Leon Shmueli"`` and domain ``leonai.io``
    therefore produces ``["leon@leonai.io", "leon.shmueli@leonai.io",
    "lshmueli@leonai.io", "leons@leonai.io", "info@leonai.io"]``.

    The domain must have active MX records; otherwise an empty list is
    returned.  Single-token names produce only the ``{first}@`` and
    ``info@`` patterns.  Duplicate addresses are removed while preserving
    probability order.

    Args:
        contact_name: The contact's full name (may be empty).
        domain: The company domain (bare, URL, or ``www.`` form accepted).

    Returns:
        list[str]: The ordered candidate email addresses, or ``[]`` when
            the domain is invalid or has no active mail servers.
    """
    cleaned_domain = _clean_domain(domain)
    if not cleaned_domain or "." not in cleaned_domain:
        return []

    if not domain_has_mail_server(cleaned_domain):
        return []

    first, last = parse_name(contact_name)

    candidates: list[str] = []
    if first:
        candidates.append(f"{first}@{cleaned_domain}")
    if first and last:
        candidates.append(f"{first}.{last}@{cleaned_domain}")
        candidates.append(f"{first[0]}{last}@{cleaned_domain}")
        candidates.append(f"{first}{last[0]}@{cleaned_domain}")
    candidates.append(f"info@{cleaned_domain}")

    # Deduplicate while preserving probability order.
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered