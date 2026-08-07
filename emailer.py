"""SMTP execution and throttling layer for B2B Substrate.

This module implements the outbound email dispatch engine.  It sends
plain-text cold emails through a standard SMTP relay (Brevo, Resend, or
any other provider), enforces a strict rolling 24-hour daily send ceiling
capped at 20 emails, logs exact dispatch timestamps in ``leads.db``, and
calculates follow-up due dates at +3 and +10 business days.

The module exposes an :class:`Emailer` class that wraps the SMTP client
and provides dispatch methods for the first email, follow-up 1, and the
breakup email.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

from config import (
    DAILY_SEND_CAP,
    # GITHUB_URL,
    # LINKEDIN_URL,
    SmtpConfig,
    load_config,
)
from database import Database

# ---------------------------------------------------------------------------
# Dispatch result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchResult:
    """Result of a single email dispatch attempt.

    Attributes:
        lead_id: The lead's primary key.
        email_type: The type of email dispatched (``EMAIL_1``,
            ``FOLLOWUP_1``, or ``BREAKUP``).
        success: ``True`` when the email was sent successfully.
        reason: A human-readable explanation of the outcome.
    """

    lead_id: int
    email_type: str
    success: bool
    reason: str


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------


def build_email_1_body(custom_pitch: str) -> str:
    """Build the plain-text body for the first cold email.

    The body is the generated custom pitch followed by the signature
    block with exactly two plain-text URLs.

    Args:
        custom_pitch: The generated 3-sentence custom pitch.

    Returns:
        str: The complete plain-text email body.
    """
    return (
        f"{custom_pitch}\n\n"
        "Rob Rowan"
        # f"\n{GITHUB_URL}"
        # f"\n{LINKEDIN_URL}"
    )


def build_followup_1_body(company_name: str) -> str:
    """Build the plain-text body for the 3-day follow-up email.

    Args:
        company_name: The recipient's company name.

    Returns:
        str: The complete plain-text follow-up email body.
    """
    return (
        f"Hey there (first_name),\n\n"
        f"quick follow-up on this. Still open to taking a look"
        f"at custom Python overflow capacity for {company_name},"
        f"or is your backend dev setup fully locked in right now? \n\n"
        "Rob Rowan"
        # f"\n{GITHUB_URL}"
        # f"\n{LINKEDIN_URL}"
    )


def build_breakup_body(company_name: str) -> str:
    """Build the plain-text body for the 10-day breakup email.

    Args:
        company_name: The recipient's company name.

    Returns:
        str: The complete plain-text breakup email body.
    """
    return (
        f"Hi there (first_name),\n\n"
        f"I know backend engineering support may not be a priority for "
        f"{company_name} right now.  I will close this thread, but if "
        f"your team ever needs help with integrations, scaling, or "
        f"technical debt, feel free to reach out.\n\n"
        "Rob Rowan"
        # f"\n{GITHUB_URL}"
        # f"\n{LINKEDIN_URL}"
    )


# ---------------------------------------------------------------------------
# Emailer class
# ---------------------------------------------------------------------------


class Emailer:
    """SMTP email dispatcher with rolling 24-hour send throttling.

    The emailer enforces a hard ceiling of ``DAILY_SEND_CAP`` emails per
    rolling 24-hour window.  The count includes first emails, follow-up
    emails, and breakup emails combined.  When the cap is reached, all
    further dispatch attempts are rejected.

    Attributes:
        db: The active database connection.
        smtp: The SMTP relay configuration.
        daily_send_cap: The maximum number of emails allowed per rolling
            24-hour window.
    """

    def __init__(
        self,
        db: Database,
        *,
        smtp: SmtpConfig | None = None,
        daily_send_cap: int = DAILY_SEND_CAP,
    ) -> None:
        """Initialize the email dispatcher.

        Args:
            db: The active database connection.
            smtp: The SMTP relay configuration.  When ``None``, the
                application configuration is loaded and its SMTP settings
                are used.
            daily_send_cap: The maximum number of emails allowed per
                rolling 24-hour window.
        """
        config = load_config()
        self.db: Database = db
        self.smtp: SmtpConfig = smtp or config.smtp
        self.daily_send_cap: int = daily_send_cap

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_email_1(self, lead_id: int) -> DispatchResult:
        """Dispatch the first cold email for a lead.

        The lead must be in the ``QUALIFIED`` state and have a custom
        pitch.  On success, the lead transitions to ``EMAIL_1_SENT`` and
        follow-up 1 is scheduled at +3 business days.

        Args:
            lead_id: The lead's primary key.

        Returns:
            DispatchResult: The dispatch outcome.
        """
        lead = self.db.get_lead(lead_id)
        if lead is None:
            return DispatchResult(
                lead_id=lead_id,
                email_type="EMAIL_1",
                success=False,
                reason="Lead not found.",
            )

        if lead.get("status") != "QUALIFIED":
            return DispatchResult(
                lead_id=lead_id,
                email_type="EMAIL_1",
                success=False,
                reason=(
                    f"Lead is in state {lead.get('status')}, not QUALIFIED."
                ),
            )

        custom_pitch = lead.get("custom_pitch") or ""
        if not custom_pitch:
            return DispatchResult(
                lead_id=lead_id,
                email_type="EMAIL_1",
                success=False,
                reason="Lead has no custom pitch.",
            )

        if not self._can_send():
            return DispatchResult(
                lead_id=lead_id,
                email_type="EMAIL_1",
                success=False,
                reason="Daily send cap reached.",
            )

        subject = (
            lead.get("custom_subject")
            or lead.get("subject")
            or "quick dev question"
        )
        body = build_email_1_body(custom_pitch)
        success, reason = self._dispatch(
            to_email=str(lead["verified_email"]),
            to_name=str(lead.get("contact_name") or lead["company_name"]),
            subject=subject,
            body=body,
        )

        if not success:
            return DispatchResult(
                lead_id=lead_id,
                email_type="EMAIL_1",
                success=False,
                reason=reason,
            )

        self.db.mark_email_1_sent(lead_id)
        return DispatchResult(
            lead_id=lead_id,
            email_type="EMAIL_1",
            success=True,
            reason="Email 1 dispatched successfully.",
        )

    def send_followup_1(self, lead_id: int) -> DispatchResult:
        """Dispatch the 3-day follow-up email for a lead.

        The lead must be in the ``FOLLOWUP_1_DUE`` state.  On success,
        the lead transitions to ``FOLLOWUP_1_SENT`` and the breakup email
        is scheduled at +10 business days.

        Args:
            lead_id: The lead's primary key.

        Returns:
            DispatchResult: The dispatch outcome.
        """
        lead = self.db.get_lead(lead_id)
        if lead is None:
            return DispatchResult(
                lead_id=lead_id,
                email_type="FOLLOWUP_1",
                success=False,
                reason="Lead not found.",
            )

        if lead.get("status") != "FOLLOWUP_1_DUE":
            return DispatchResult(
                lead_id=lead_id,
                email_type="FOLLOWUP_1",
                success=False,
                reason=(
                    f"Lead is in state {lead.get('status')}, not "
                    "FOLLOWUP_1_DUE."
                ),
            )

        if not self._can_send():
            return DispatchResult(
                lead_id=lead_id,
                email_type="FOLLOWUP_1",
                success=False,
                reason="Daily send cap reached.",
            )

        subject = (
            lead.get("custom_subject")
            or lead.get("subject")
            or "quick dev question"
        )
        body = build_followup_1_body(str(lead["company_name"]))
        success, reason = self._dispatch(
            to_email=str(lead["verified_email"]),
            to_name=str(lead.get("contact_name") or lead["company_name"]),
            subject=subject,
            body=body,
        )

        if not success:
            return DispatchResult(
                lead_id=lead_id,
                email_type="FOLLOWUP_1",
                success=False,
                reason=reason,
            )

        self.db.mark_followup_1_sent(lead_id)
        return DispatchResult(
            lead_id=lead_id,
            email_type="FOLLOWUP_1",
            success=True,
            reason="Follow-up 1 dispatched successfully.",
        )

    def send_breakup(self, lead_id: int) -> DispatchResult:
        """Dispatch the 10-day breakup email for a lead.

        The lead must be in the ``FOLLOWUP_2_DUE`` state.  On success,
        the lead transitions to ``BREAKUP_SENT``.

        Args:
            lead_id: The lead's primary key.

        Returns:
            DispatchResult: The dispatch outcome.
        """
        lead = self.db.get_lead(lead_id)
        if lead is None:
            return DispatchResult(
                lead_id=lead_id,
                email_type="BREAKUP",
                success=False,
                reason="Lead not found.",
            )

        if lead.get("status") != "FOLLOWUP_2_DUE":
            return DispatchResult(
                lead_id=lead_id,
                email_type="BREAKUP",
                success=False,
                reason=(
                    f"Lead is in state {lead.get('status')}, not "
                    "FOLLOWUP_2_DUE."
                ),
            )

        if not self._can_send():
            return DispatchResult(
                lead_id=lead_id,
                email_type="BREAKUP",
                success=False,
                reason="Daily send cap reached.",
            )

        body = build_breakup_body(str(lead["company_name"]))
        success, reason = self._dispatch(
            to_email=str(lead["verified_email"]),
            to_name=str(lead.get("contact_name") or lead["company_name"]),
            subject="Closing the loop",
            body=body,
        )

        if not success:
            return DispatchResult(
                lead_id=lead_id,
                email_type="BREAKUP",
                success=False,
                reason=reason,
            )

        self.db.mark_breakup_sent(lead_id)
        return DispatchResult(
            lead_id=lead_id,
            email_type="BREAKUP",
            success=True,
            reason="Breakup email dispatched successfully.",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _can_send(self) -> bool:
        """Return ``True`` when the rolling 24-hour send cap is not met.

        Returns:
            bool: ``True`` when the number of emails dispatched in the
                last 24 hours is below the daily send cap.
        """
        sent_in_window = self.db.get_sent_in_window_count(window_hours=24)
        return sent_in_window < self.daily_send_cap

    def _dispatch(
        self,
        *,
        to_email: str,
        to_name: str,
        subject: str,
        body: str,
    ) -> tuple[bool, str]:
        """Send a plain-text email through the SMTP relay.

        Args:
            to_email: The recipient's email address.
            to_name: The recipient's display name.
            subject: The email subject line.
            body: The plain-text email body.

        Returns:
            tuple[bool, str]: A tuple of success flag and a reason
                string.

        Raises:
            RuntimeError: If the SMTP relay is not configured.
        """
        if not self.smtp.is_configured:
            raise RuntimeError(
                "SMTP relay is not configured. Set SMTP_HOST, SMTP_PORT, "
                "SMTP_USERNAME, SMTP_PASSWORD, and SMTP_FROM_EMAIL "
                "environment variables."
            )

        message = EmailMessage()
        message["From"] = formataddr(
            (self.smtp.from_name, self.smtp.from_email)
        )
        message["To"] = formataddr((to_name, to_email))
        message["Subject"] = subject
        message.set_content(body)

        try:
            if self.smtp.port == 465:
                with smtplib.SMTP_SSL(self.smtp.host, self.smtp.port, timeout=30) as server:
                    server.login(self.smtp.username, self.smtp.password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(self.smtp.host, self.smtp.port, timeout=30) as server:
                    server.starttls()
                    server.login(self.smtp.username, self.smtp.password)
                    server.send_message(message)
            return True, "Email sent successfully."
        except smtplib.SMTPException as exc:
            return False, f"SMTP error: {exc}"
        except OSError as exc:
            return False, f"Network error: {exc}"