"""Deliverability firewall and email verification for B2B Substrate.

This module implements the email verification layer that protects sender
domain reputation.  It performs DNS MX record lookups to confirm that a
domain can receive email, and applies strict pattern-based filtering to
reject catch-all, disposable, role-based, and malformed email addresses.

The module exposes a :class:`EmailVerifier` class that wraps the
verification logic and provides a single ``verify`` method used by the
ingestion pipeline and the Streamlit application layer.  It also exposes
:func:`verify_mailbox` for deep SMTP mailbox verification that connects
directly to the primary MX host and issues ``RCPT TO`` commands to
determine whether a specific mailbox is deliverable.
"""

from __future__ import annotations

import random
import re
import smtplib
import socket
import time
from dataclasses import dataclass, field
from typing import Final

from config import (
    DISPOSABLE_DOMAINS,
    ROLE_BASED_LOCAL_PARTS,
    VERIFY_MX_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Regular expression patterns
# ---------------------------------------------------------------------------

# Matches a syntactically valid email address.
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
)

# Matches a syntactically valid domain name.
_DOMAIN_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
    r"[A-Za-z0-9])?\.)+[A-Za-z]{2,}$"
)

# Matches a local part that contains a plus-address suffix.
_PLUS_ADDRESS_RE: Final[re.Pattern[str]] = re.compile(r"\+[^@]*@")

# Matches a local part that is entirely numeric.
_NUMERIC_LOCAL_RE: Final[re.Pattern[str]] = re.compile(r"^\d+$")

# Sender address used for SMTP mailbox verification.  This is a
# deliberately generic address that most mail servers accept for the
# ``MAIL FROM`` command during the SMTP conversation.
_VERIFY_SENDER: Final[str] = "verify@b2bsubstrate.local"

# SMTP port used for direct mailbox verification.
_SMTP_VERIFY_PORT: Final[int] = 25

# Tracks the last SMTP ping time (timestamp) per domain to prevent throttling.
_LAST_MX_PINGS: dict[str, float] = {}

# ---------------------------------------------------------------------------
# Verification result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationResult:
    """Result of an email verification check.

    Attributes:
        email: The email address that was verified.
        is_valid: ``True`` when the email passed all verification checks.
        reason: A human-readable explanation of the result.
        mx_records: The list of MX hostnames found for the domain, or an
            empty list when no MX records exist.
    """

    email: str
    is_valid: bool
    reason: str
    mx_records: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------


def is_valid_email_format(email: str) -> bool:
    """Return ``True`` when the email has a syntactically valid format.

    Args:
        email: The email address to validate.

    Returns:
        bool: ``True`` when the email matches the standard RFC-style
            pattern.
    """
    if not email or len(email) > 254:
        return False
    return _EMAIL_RE.match(email) is not None


def is_valid_domain_format(domain: str) -> bool:
    """Return ``True`` when the domain has a syntactically valid format.

    Args:
        domain: The domain name to validate.

    Returns:
        bool: ``True`` when the domain matches the standard DNS pattern.
    """
    if not domain or len(domain) > 253:
        return False
    return _DOMAIN_RE.match(domain) is not None


def is_disposable_domain(domain: str) -> bool:
    """Return ``True`` when the domain is a known disposable email domain.

    Args:
        domain: The domain name to check.

    Returns:
        bool: ``True`` when the domain appears in the disposable domain
            blocklist.
    """
    return domain.lower() in DISPOSABLE_DOMAINS


def is_role_based_email(email: str) -> bool:
    """Return ``True`` when the email uses a role-based local part.

    Role-based addresses such as ``info@``, ``sales@``, and ``support@``
    are considered risky for cold outreach because they are typically
    monitored by multiple people or ignored entirely.

    Args:
        email: The email address to check.

    Returns:
        bool: ``True`` when the local part is a known role-based address.
    """
    local_part = email.split("@", 1)[0].lower()
    return local_part in ROLE_BASED_LOCAL_PARTS


def is_risky_pattern(email: str) -> bool:
    """Return ``True`` when the email matches a risky pattern.

    Risky patterns include plus-addresses (``user+tag@domain``), numeric
    local parts (``12345@domain``), and local parts that are entirely
    numeric.

    Args:
        email: The email address to check.

    Returns:
        bool: ``True`` when the email matches a risky pattern.
    """
    local_part = email.split("@", 1)[0]
    if _PLUS_ADDRESS_RE.search(email):
        return True
    if _NUMERIC_LOCAL_RE.match(local_part):
        return True
    return False


# ---------------------------------------------------------------------------
# DNS MX lookup
# ---------------------------------------------------------------------------


def lookup_mx_records(domain: str) -> tuple[str, ...]:
    """Look up the MX records for a domain using DNS.

    The lookup uses the ``dnspython`` library when available.  If
    ``dnspython`` is not installed, the function falls back to a
    ``nslookup`` subprocess call on Windows or a ``dig`` subprocess call
    on Unix-like systems.

    Args:
        domain: The domain name to look up.

    Returns:
        tuple[str, ...]: A tuple of MX hostnames, or an empty tuple when
            no MX records exist or the lookup fails.

    Raises:
        ValueError: If the domain format is invalid.
    """
    if not is_valid_domain_format(domain):
        raise ValueError(f"Invalid domain format: {domain}")

    try:
        import dns.resolver

        answers = dns.resolver.resolve(domain, "MX", lifetime=VERIFY_MX_TIMEOUT)
        mx_hosts = sorted(
            (str(rdata.exchange).rstrip(".") for rdata in answers),
            key=lambda host: host,
        )
        return tuple(mx_hosts)
    except ImportError:
        return _lookup_mx_subprocess(domain)
    except Exception:
        return ()


def _lookup_mx_subprocess(domain: str) -> tuple[str, ...]:
    """Fallback MX lookup using a subprocess DNS tool.

    Args:
        domain: The domain name to look up.

    Returns:
        tuple[str, ...]: A tuple of MX hostnames, or an empty tuple when
            the lookup fails.
    """
    import shutil
    import subprocess
    import sys

    if sys.platform.startswith("win"):
        tool = shutil.which("nslookup")
        if not tool:
            return ()
        try:
            result = subprocess.run(
                [tool, "-type=MX", domain],
                capture_output=True,
                text=True,
                timeout=VERIFY_MX_TIMEOUT,
                check=False,
            )
            lines = result.stdout.splitlines()
            mx_hosts: list[str] = []
            for line in lines:
                stripped = line.strip()
                if "mail exchanger" in stripped.lower():
                    parts = stripped.split()
                    if parts:
                        mx_hosts.append(parts[-1].rstrip("."))
            return tuple(sorted(set(mx_hosts)))
        except Exception:
            return ()

    tool = shutil.which("dig")
    if not tool:
        return ()
    try:
        result = subprocess.run(
            [tool, "+short", "MX", domain],
            capture_output=True,
            text=True,
            timeout=VERIFY_MX_TIMEOUT,
            check=False,
        )
        mx_hosts = []
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if parts:
                mx_hosts.append(parts[-1].rstrip("."))
        return tuple(sorted(set(mx_hosts)))
    except Exception:
        return ()


# ---------------------------------------------------------------------------
# Deep SMTP mailbox verification
# ---------------------------------------------------------------------------


def verify_mailbox(email: str) -> dict:
    """Perform deep SMTP mailbox verification for an email address.

    The verification performs the following steps:

    1. Validate the email format.
    2. Look up MX records for the domain using ``dnspython``.
    3. Connect directly to the primary MX host via port 25 using
       ``smtplib``.
    4. Run a catch-all test: issue ``RCPT TO`` for a random, non-existent
       address (``random_test_xyz123@{domain}``).  If the response is
       250, the domain is marked as a catch-all.
    5. Issue ``RCPT TO: {email}`` and classify the response:
       - ``250`` + NOT catch-all -> ``VERIFIED_DELIVERABLE``
       - ``250`` + IS catch-all -> ``RISKY_CATCHALL``
       - ``550`` or ``551`` -> ``INVALID_USER``
       - Connection timeout / blocked -> ``UNKNOWN_UNVERIFIED``

    Args:
        email: The email address to verify.

    Returns:
        dict: A dictionary with the following keys:

            * ``email`` — the normalized email address.
            * ``status`` — one of ``VERIFIED_DELIVERABLE``,
              ``RISKY_CATCHALL``, ``INVALID_USER``, or
              ``UNKNOWN_UNVERIFIED``.
            * ``is_catchall`` — ``True`` when the domain accepts all
              addresses (catch-all).
            * ``reason`` — a human-readable explanation of the result.
            * ``mx_host`` — the primary MX host used for the SMTP
              conversation, or ``None`` when no MX records exist.
    """
    email = email.strip().lower()

    if not is_valid_email_format(email):
        return {
            "email": email,
            "status": "INVALID_USER",
            "is_catchall": False,
            "reason": "Invalid email format.",
            "mx_host": None,
        }

    domain = email.split("@", 1)[1]

    # Look up MX records for the domain.
    mx_records = lookup_mx_records(domain)
    if not mx_records:
        return {
            "email": email,
            "status": "UNKNOWN_UNVERIFIED",
            "is_catchall": False,
            "reason": "Domain has no MX records and cannot receive email.",
            "mx_host": None,
        }

    primary_mx = mx_records[0]

    # Stagger the pings to prevent remote mail server throttling.
    # "Put a 1-to-2 second time.sleep() delay between pattern checks"
    now = time.time()
    if domain in _LAST_MX_PINGS:
        elapsed = now - _LAST_MX_PINGS[domain]
        delay = random.uniform(1.0, 2.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
    _LAST_MX_PINGS[domain] = time.time()

    try:
        with smtplib.SMTP(primary_mx, _SMTP_VERIFY_PORT, timeout=VERIFY_MX_TIMEOUT) as server:
            server.ehlo()

            # Send MAIL FROM with the verification sender.
            mail_code, _ = server.mail(_VERIFY_SENDER)
            if mail_code not in (250, 251, 252):
                return {
                    "email": email,
                    "status": "UNKNOWN_UNVERIFIED",
                    "is_catchall": False,
                    "reason": (
                        f"MAIL FROM rejected by {primary_mx} with "
                        f"code {mail_code}."
                    ),
                    "mx_host": primary_mx,
                }

            # Catch-all test: issue RCPT TO for a random non-existent
            # address.  A 250 response indicates the domain accepts all
            # addresses (catch-all).
            random_address = f"random_test_xyz123@{domain}"
            catchall_code, _ = server.rcpt(random_address)
            is_catchall = catchall_code == 250

            # Issue RCPT TO for the actual email address.
            rcpt_code, _ = server.rcpt(email)

            if rcpt_code == 250:
                if is_catchall:
                    status = "RISKY_CATCHALL"
                else:
                    status = "VERIFIED_DELIVERABLE"
            elif rcpt_code in (550, 551):
                status = "INVALID_USER"
            else:
                status = "UNKNOWN_UNVERIFIED"

            return {
                "email": email,
                "status": status,
                "is_catchall": is_catchall,
                "reason": (
                    f"SMTP response code {rcpt_code} from {primary_mx}."
                ),
                "mx_host": primary_mx,
            }
    except (TimeoutError, socket.timeout) as exc:
        return {
            "email": email,
            "status": "UNVERIFIED_TIMEOUT",
            "is_catchall": False,
            "reason": f"SMTP connection to {primary_mx} timed out: {exc}",
            "mx_host": primary_mx,
        }
    except (smtplib.SMTPException, OSError) as exc:
        if "timed out" in str(exc).lower():
            return {
                "email": email,
                "status": "UNVERIFIED_TIMEOUT",
                "is_catchall": False,
                "reason": f"SMTP connection to {primary_mx} timed out: {exc}",
                "mx_host": primary_mx,
            }
        return {
            "email": email,
            "status": "UNKNOWN_UNVERIFIED",
            "is_catchall": False,
            "reason": f"SMTP connection to {primary_mx} failed: {exc}",
            "mx_host": primary_mx,
        }


# ---------------------------------------------------------------------------
# Verifier class
# ---------------------------------------------------------------------------


class EmailVerifier:
    """Email deliverability firewall for B2B Substrate.

    The verifier applies a strict sequence of checks to every email
    address before it is accepted into the outreach pipeline:

    1. Syntactic format validation.
    2. Disposable domain blocklist.
    3. Role-based local part detection.
    4. Risky pattern detection (plus-addresses, numeric locals).
    5. DNS MX record lookup to confirm the domain can receive email.

    Attributes:
        require_mx: When ``True``, a domain must have at least one MX
            record to pass verification.
    """

    def __init__(self, *, require_mx: bool = True) -> None:
        """Initialize the email verifier.

        Args:
            require_mx: When ``True``, a domain must have at least one MX
                record to pass verification.  Defaults to ``True``.
        """
        self.require_mx: bool = require_mx

    def verify(self, email: str) -> VerificationResult:
        """Verify an email address against all deliverability checks.

        Args:
            email: The email address to verify.

        Returns:
            VerificationResult: The verification outcome with a reason
                and any MX records found.
        """
        email = email.strip().lower()

        if not is_valid_email_format(email):
            return VerificationResult(
                email=email,
                is_valid=False,
                reason="Invalid email format.",
            )

        domain = email.split("@", 1)[1]

        if is_disposable_domain(domain):
            return VerificationResult(
                email=email,
                is_valid=False,
                reason="Disposable email domain is not allowed.",
            )

        if is_role_based_email(email):
            return VerificationResult(
                email=email,
                is_valid=False,
                reason="Role-based email address is not allowed.",
            )

        if is_risky_pattern(email):
            return VerificationResult(
                email=email,
                is_valid=False,
                reason="Email matches a risky pattern.",
            )

        mx_records = lookup_mx_records(domain)

        if self.require_mx and not mx_records:
            return VerificationResult(
                email=email,
                is_valid=False,
                reason="Domain has no MX records and cannot receive email.",
            )

        return VerificationResult(
            email=email,
            is_valid=True,
            reason="Email passed all verification checks.",
            mx_records=mx_records,
        )

    def verify_domain(self, domain: str) -> VerificationResult:
        """Verify a domain's ability to receive email.

        Args:
            domain: The domain name to verify.

        Returns:
            VerificationResult: The verification result with MX records.
        """
        domain = domain.strip().lower()

        if not is_valid_domain_format(domain):
            return VerificationResult(
                email=domain,
                is_valid=False,
                reason="Invalid domain format.",
            )

        if is_disposable_domain(domain):
            return VerificationResult(
                email=domain,
                is_valid=False,
                reason="Disposable email domain is not allowed.",
            )

        mx_records = lookup_mx_records(domain)

        if self.require_mx and not mx_records:
            return VerificationResult(
                email=domain,
                is_valid=False,
                reason="Domain has no MX records and cannot receive email.",
            )

        return VerificationResult(
            email=domain,
            is_valid=True,
            reason="Domain passed all verification checks.",
            mx_records=mx_records,
        )