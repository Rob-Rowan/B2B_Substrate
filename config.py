"""Application configuration and credential loading for B2B Substrate.

This module centralizes all configuration values used across the B2B
Substrate application, including Google Cloud Platform (GCP) service
account credential resolution, database paths, daily send limits,
follow-up scheduling parameters, SMTP credentials, email verification
settings, and ingestion timeouts.

The credential resolution follows the standard Google Cloud convention:
the ``GOOGLE_APPLICATION_CREDENTIALS`` environment variable is checked
first, followed by a user-specified local path, and finally a locally
configured fallback path defined in this module.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()
# ---------------------------------------------------------------------------
# Path and environment constants
# ---------------------------------------------------------------------------

BASE_DIR: Final[Path] = Path(__file__).resolve().parent

DATABASE_PATH: Final[Path] = BASE_DIR / "leads.db"

# Local fallback path for the GCP service account JSON key file.  This is
# used only when the ``GOOGLE_APPLICATION_CREDENTIALS`` environment variable
# is not set and the user-specified path does not exist.
LOCAL_SERVICE_ACCOUNT_PATH: Final[Path] = BASE_DIR / "service_account.json"

# User-specified GCP service account JSON key file path.  This is checked
# after the environment variable and before the local fallback. CHANGE THIS PATH TO POINT TO YOUR OWN SERVICE ACCOUNT JSON FILE.
USER_GCP_SERVICE_ACCOUNT_PATH: Final[Path] = Path(
    r"C:\DOCUMENTS\API Keys\rob-vertex-production-3874a8c3172b.json"
)

# Vertex AI project and location identifiers.  These should be overridden
# via environment variables in production deployments.
DEFAULT_GCP_PROJECT: Final[str] = os.getenv("GCP_PROJECT", "rob-vertex-production")
DEFAULT_GCP_LOCATION: Final[str] = os.getenv("GCP_LOCATION", "global")

# Gemini model identifier used by the LLM engine.
GEMINI_MODEL_NAME: Final[str] = os.getenv("GEMINI_MODEL_NAME", "gemini-3.6-flash")

# ---------------------------------------------------------------------------
# Outreach scheduling constants
# ---------------------------------------------------------------------------

# Hard ceiling on the number of cold emails that may be sent per rolling
# 24-hour window.  This cap guarantees the application never exceeds the
# daily send budget regardless of how many leads are queued.
DAILY_SEND_CAP: Final[int] = 20

# Business-day offsets for the follow-up sequence.
FOLLOWUP_1_BUSINESS_DAYS: Final[int] = 3
BREAKUP_BUSINESS_DAYS: Final[int] = 10

# ---------------------------------------------------------------------------
# Email signature constants
# ---------------------------------------------------------------------------

# Plain-text URLs used in the cold email signature.  These are rendered
# exactly as written with no HTML anchor tags or markdown formatting.
# NOTE: Commented out per user request so they are no longer attached to
# outbound emails.
# GITHUB_URL: Final[str] = "https://github.com/Rob-Rowan"
# LINKEDIN_URL: Final[str] = "www.linkedin.com/in/rob-rowan-dev"

# ---------------------------------------------------------------------------
# SMTP settings
# ---------------------------------------------------------------------------

# SMTP credentials for the outbound mail relay.  Override via environment
# variables when using Brevo, Resend, or any other SMTP provider.
SMTP_HOST: Final[str] = os.getenv("SMTP_HOST", "smtp-relay.brevo.com")
SMTP_PORT: Final[int] = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME: Final[str] = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD: Final[str] = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL: Final[str] = os.getenv("SMTP_FROM_EMAIL", "")
SMTP_FROM_NAME: Final[str] = os.getenv("SMTP_FROM_NAME", "Rob Rowan")

# ---------------------------------------------------------------------------
# Email verification settings
# ---------------------------------------------------------------------------

# DNS MX lookup timeout in seconds.
VERIFY_MX_TIMEOUT: Final[float] = float(os.getenv("VERIFY_MX_TIMEOUT", "5.0"))

# Known disposable email domains that should be rejected during verification.
DISPOSABLE_DOMAINS: Final[tuple[str, ...]] = (
    "mailinator.com",
    "yopmail.com",
    "guerrillamail.com",
    "sharklasers.com",
    "temp-mail.org",
    "10minutemail.com",
    "mail.tm",
    "mailnesia.com",
    "getnada.com",
    "dispostable.com",
    "mailcatch.com",
    "tempmail.com",
    "fakeinbox.com",
    "trashmail.com",
    "tempmailo.com",
    "mintemail.com",
    "mohmal.com",
    "emailondeck.com",
    "throwawaymail.com",
    "spamgourmet.com",
)

# Role-based email local parts that indicate a generic inbox rather than a
# personal contact.  These are considered risky for cold outreach.
ROLE_BASED_LOCAL_PARTS: Final[tuple[str, ...]] = (
    "info",
    "sales",
    "support",
    "contact",
    "hello",
    "admin",
    "office",
    "enquiries",
    "inquiries",
    "team",
    "careers",
    "jobs",
    "hr",
    "billing",
    "accounts",
    "marketing",
    "press",
    "media",
    "pr",
    "webmaster",
    "postmaster",
    "abuse",
    "noreply",
    "no-reply",
    "mailer",
    "mail",
    "service",
    "services",
    "general",
    "reception",
    "frontdesk",
    "info",
)

# ---------------------------------------------------------------------------
# Ingestion settings
# ---------------------------------------------------------------------------

# HTTP timeout for ingestion requests in seconds.
INGESTION_TIMEOUT: Final[float] = float(os.getenv("INGESTION_TIMEOUT", "30.0"))

# Maximum number of bytes to accept from a single ingestion source.
INGESTION_MAX_BYTES: Final[int] = int(os.getenv("INGESTION_MAX_BYTES", "5242880"))

# ---------------------------------------------------------------------------
# Lead state machine
# ---------------------------------------------------------------------------

LEAD_STATE_UNPROCESSED: Final[str] = "UNPROCESSED"
LEAD_STATE_QUALIFIED: Final[str] = "QUALIFIED"
LEAD_STATE_EMAIL_1_SENT: Final[str] = "EMAIL_1_SENT"
LEAD_STATE_FOLLOWUP_1_DUE: Final[str] = "FOLLOWUP_1_DUE"
LEAD_STATE_FOLLOWUP_1_SENT: Final[str] = "FOLLOWUP_1_SENT"
LEAD_STATE_FOLLOWUP_2_DUE: Final[str] = "FOLLOWUP_2_DUE"
LEAD_STATE_BREAKUP_SENT: Final[str] = "BREAKUP_SENT"
LEAD_STATE_REPLIED: Final[str] = "REPLIED"
LEAD_STATE_MEETING_BOOKED: Final[str] = "MEETING_BOOKED"
LEAD_STATE_SKIPPED: Final[str] = "SKIPPED"
LEAD_STATE_BOUNCED: Final[str] = "BOUNCED"

LEAD_STATES: Final[tuple[str, ...]] = (
    LEAD_STATE_UNPROCESSED,
    LEAD_STATE_QUALIFIED,
    LEAD_STATE_EMAIL_1_SENT,
    LEAD_STATE_FOLLOWUP_1_DUE,
    LEAD_STATE_FOLLOWUP_1_SENT,
    LEAD_STATE_FOLLOWUP_2_DUE,
    LEAD_STATE_BREAKUP_SENT,
    LEAD_STATE_REPLIED,
    LEAD_STATE_MEETING_BOOKED,
    LEAD_STATE_SKIPPED,
    LEAD_STATE_BOUNCED,
)

# ---------------------------------------------------------------------------
# Prompt injection signatures
# ---------------------------------------------------------------------------

# Known prompt injection phrases that should be stripped from raw scraped
# content before it is forwarded to the LLM engine.
PROMPT_INJECTION_SIGNATURES: Final[tuple[str, ...]] = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard previous instructions",
    "disregard all previous instructions",
    "forget previous instructions",
    "forget all previous instructions",
    "you are now",
    "you are not",
    "system prompt",
    "override instructions",
    "new instructions",
    "do not follow",
    "ignore the above",
    "ignore everything above",
    "pretend you are",
    "act as if",
    "reveal your instructions",
    "print your instructions",
    "show your instructions",
    "output your instructions",
    "repeat your instructions",
    "jailbreak",
    "developer mode",
    "dan mode",
)

# ---------------------------------------------------------------------------
# Pydantic configuration models
# ---------------------------------------------------------------------------


class CredentialConfig(BaseModel):
    """Resolved GCP service account credential configuration.

    Attributes:
        service_account_path: Absolute path to the GCP service account JSON
            key file, or ``None`` if no credentials could be located.
        gcp_project: GCP project identifier used by the Vertex AI client.
        gcp_location: GCP region/location used by the Vertex AI client.
        gemini_model: Gemini model identifier used for LLM inference.
    """

    service_account_path: Path | None = Field(
        default=None,
        description="Absolute path to the GCP service account JSON key file.",
    )
    gcp_project: str = Field(
        default=DEFAULT_GCP_PROJECT,
        description="GCP project identifier for Vertex AI.",
    )
    gcp_location: str = Field(
        default=DEFAULT_GCP_LOCATION,
        description="GCP region/location for Vertex AI.",
    )
    gemini_model: str = Field(
        default=GEMINI_MODEL_NAME,
        description="Gemini model identifier for LLM inference.",
    )

    @property
    def has_credentials(self) -> bool:
        """Return ``True`` when a service account path has been resolved.

        Returns:
            bool: ``True`` if ``service_account_path`` is not ``None``.
        """
        return self.service_account_path is not None


class SmtpConfig(BaseModel):
    """SMTP relay configuration for outbound email dispatch.

    Attributes:
        host: SMTP relay hostname.
        port: SMTP relay port.
        username: SMTP authentication username.
        password: SMTP authentication password.
        from_email: Sender email address.
        from_name: Sender display name.
    """

    host: str = Field(
        default=SMTP_HOST,
        description="SMTP relay hostname.",
    )
    port: int = Field(
        default=SMTP_PORT,
        description="SMTP relay port.",
    )
    username: str = Field(
        default=SMTP_USERNAME,
        description="SMTP authentication username.",
    )
    password: str = Field(
        default=SMTP_PASSWORD,
        description="SMTP authentication password.",
    )
    from_email: str = Field(
        default=SMTP_FROM_EMAIL,
        description="Sender email address.",
    )
    from_name: str = Field(
        default=SMTP_FROM_NAME,
        description="Sender display name.",
    )

    @property
    def is_configured(self) -> bool:
        """Return ``True`` when the SMTP relay is fully configured.

        Returns:
            bool: ``True`` when host, username, password, and from email
                are all non-empty.
        """
        return bool(
            self.host and self.username and self.password and self.from_email
        )


class AppConfig(BaseModel):
    """Top-level application configuration container.

    Attributes:
        database_path: Absolute path to the SQLite database file.
        daily_send_cap: Maximum number of cold emails allowed per day.
        followup_1_business_days: Business-day offset for follow-up 1.
        breakup_business_days: Business-day offset for the breakup email.
        credentials: Resolved GCP credential configuration.
        smtp: SMTP relay configuration.
    """

    database_path: Path = Field(
        default=DATABASE_PATH,
        description="Absolute path to the SQLite database file.",
    )
    daily_send_cap: int = Field(
        default=DAILY_SEND_CAP,
        description="Maximum number of cold emails allowed per day.",
    )
    followup_1_business_days: int = Field(
        default=FOLLOWUP_1_BUSINESS_DAYS,
        description="Business-day offset for follow-up 1.",
    )
    breakup_business_days: int = Field(
        default=BREAKUP_BUSINESS_DAYS,
        description="Business-day offset for the breakup email.",
    )
    credentials: CredentialConfig = Field(
        default_factory=lambda: CredentialConfig(
            service_account_path=resolve_service_account_path()
        ),
        description="Resolved GCP credential configuration.",
    )
    smtp: SmtpConfig = Field(
        default_factory=SmtpConfig,
        description="SMTP relay configuration.",
    )


# ---------------------------------------------------------------------------
# Credential resolution helpers
# ---------------------------------------------------------------------------


def resolve_service_account_path() -> Path | None:
    """Resolve the GCP service account JSON key file path.

    The resolution order is:

    1. The ``GOOGLE_APPLICATION_CREDENTIALS`` environment variable.
    2. The user-specified path in ``USER_GCP_SERVICE_ACCOUNT_PATH``.
    3. The local ``service_account.json`` file in the project root.

    Returns:
        Path | None: The absolute path to the service account key file if
            it exists and is a file, otherwise ``None``.
    """
    env_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if env_path:
        candidate = Path(env_path).resolve()
        if candidate.is_file():
            return candidate

    if USER_GCP_SERVICE_ACCOUNT_PATH.is_file():
        return USER_GCP_SERVICE_ACCOUNT_PATH

    if LOCAL_SERVICE_ACCOUNT_PATH.is_file():
        return LOCAL_SERVICE_ACCOUNT_PATH

    return None


def load_config() -> AppConfig:
    """Load and return the application configuration.

    Returns:
        AppConfig: A fully populated application configuration object.
    """
    return AppConfig()