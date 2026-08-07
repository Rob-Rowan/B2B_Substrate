"""Google Workspace SMTP dispatch engine for B2B Substrate.

This module implements the outbound email transport layer used by the
Cold Triage Desk to send live cold-outreach emails through a Google
Workspace (or any standards-compliant STARTTLS) SMTP relay.  It is the
*only* module in this codebase that ever opens a network socket to an
SMTP server, and it has zero dependency on the ORM, the Streamlit UI,
or the Jinja2 draft engine.

Configuration is loaded exclusively from environment variables (see
``.env`` / ``.env.example``):

* ``SMTP_SERVER`` — the SMTP relay hostname. Defaults to
  ``smtp.gmail.com`` (Google Workspace / Gmail).
* ``SMTP_PORT`` — the SMTP relay port. Defaults to ``587``
  (STARTTLS).
* ``SMTP_USERNAME`` — the mailbox username used for SMTP AUTH and as
  the ``From`` address on outgoing mail.
* ``SMTP_PASSWORD`` — the mailbox password / app password used for
  SMTP AUTH.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


class EmailService:
    """A minimal, production-grade SMTP dispatch client.

    Wraps Python's native :mod:`smtplib` and
    :class:`email.mime.text.MIMEText` to send plain-text cold-outreach
    emails through a Google Workspace SMTP relay, enforcing TLS
    encryption via ``STARTTLS`` and SMTP AUTH on every send.

    Attributes:
        smtp_server: The SMTP relay hostname.
        smtp_port: The SMTP relay port.
        smtp_username: The mailbox username used for SMTP AUTH and as
            the ``From`` address on outgoing mail.
        smtp_password: The mailbox password / app password used for
            SMTP AUTH.
    """

    def __init__(
        self,
        smtp_server: str | None = None,
        smtp_port: int | None = None,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
    ) -> None:
        """Initialize the email service from explicit args or ``.env``.

        Args:
            smtp_server: Optional override for the SMTP hostname.
                Falls back to the ``SMTP_SERVER`` environment
                variable, then to ``"smtp.gmail.com"``.
            smtp_port: Optional override for the SMTP port. Falls
                back to the ``SMTP_PORT`` environment variable, then
                to ``587``.
            smtp_username: Optional override for the SMTP AUTH
                username. Falls back to the ``SMTP_USERNAME``
                environment variable.
            smtp_password: Optional override for the SMTP AUTH
                password. Falls back to the ``SMTP_PASSWORD``
                environment variable.
        """
        self.smtp_server: str = smtp_server or os.getenv(
            "SMTP_SERVER", "smtp.gmail.com"
        )
        self.smtp_port: int = int(smtp_port or os.getenv("SMTP_PORT", "587"))
        self.smtp_username: str | None = smtp_username or os.getenv(
            "SMTP_USERNAME"
        )
        self.smtp_password: str | None = smtp_password or os.getenv(
            "SMTP_PASSWORD"
        )

    def send_cold_email(
        self, to_email: str, subject: str, body_text: str
    ) -> bool:
        """Send a single plain-text cold-outreach email.

        Opens a fresh SMTP connection, upgrades it to TLS via
        ``server.starttls()``, authenticates with the configured
        Google Workspace credentials, and dispatches a
        ``text/plain`` MIME payload to ``to_email``.

        Args:
            to_email: The verified recipient email address.
            subject: The exact (possibly user-edited) subject line.
            body_text: The exact (possibly user-edited) plain-text
                body.

        Returns:
            bool: ``True`` if the SMTP server accepted the message
                for delivery (SMTP 250), ``False`` on any
                configuration error, authentication failure,
                invalid-recipient rejection, or other SMTP/network
                error.
        """
        if not to_email or not to_email.strip():
            logger.error(
                "send_cold_email: missing recipient email address."
            )
            return False

        if not self.smtp_username or not self.smtp_password:
            logger.error(
                "send_cold_email: SMTP_USERNAME/SMTP_PASSWORD are not "
                "configured; refusing to send to %s.",
                to_email,
            )
            return False

        message = MIMEText(body_text or "", "plain")
        message["Subject"] = subject or ""
        message["From"] = self.smtp_username
        message["To"] = to_email

        server: smtplib.SMTP | None = None
        try:
            server = smtplib.SMTP(
                self.smtp_server, self.smtp_port, timeout=30
            )
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.smtp_username, self.smtp_password)
            server.sendmail(
                self.smtp_username, [to_email], message.as_string()
            )
            logger.info("send_cold_email: delivered to %s.", to_email)
            return True
        except smtplib.SMTPAuthenticationError as exc:
            logger.error(
                "send_cold_email: SMTP authentication failed for user "
                "%s: %s",
                self.smtp_username,
                exc,
            )
            return False
        except smtplib.SMTPRecipientsRefused as exc:
            logger.error(
                "send_cold_email: recipient refused for %s: %s",
                to_email,
                exc,
            )
            return False
        except smtplib.SMTPException as exc:
            logger.error("send_cold_email: SMTP error sending to %s: %s", to_email, exc)
            return False
        except OSError as exc:
            logger.error(
                "send_cold_email: network/connection error sending to "
                "%s: %s",
                to_email,
                exc,
            )
            return False
        finally:
            if server is not None:
                try:
                    server.quit()
                except smtplib.SMTPException:
                    pass
