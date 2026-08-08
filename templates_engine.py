"""Lightweight Jinja2 draft-interpolation engine for B2B Substrate.

This module implements the personalized outreach draft generator used
by the Cold Triage desk.  Given a lead, it extracts the contact's
first name from ``contact_name`` and renders a default subject/body
pair through small Jinja2 templates, substituting ``first_name``,
``company_name``, and ``tech_stack``.

The engine deliberately has zero dependency on any LLM provider — it
is pure deterministic string templating.
"""

from __future__ import annotations

from dataclasses import dataclass

from jinja2 import Environment, StrictUndefined

# ---------------------------------------------------------------------------
# Default templates
# ---------------------------------------------------------------------------

#: Default subject line template for the Cold Triage desk draft.
DEFAULT_SUBJECT_TEMPLATE: str = "quick dev question"

#: Default plain-text body template for the Cold Triage desk draft.
DEFAULT_BODY_TEMPLATE: str = (
    "Hi {{ first_name }},\n\n"
    "Do you guys ever hit an execution bottleneck when client "
    "builds demand custom Python microservices,heavy pipelines, "
    "automation, or complex APIs?"
    "I’m a senior Python backend engineer. I step in as an async, "
    "100% white-label sub-contractor to take those complex API integrations "
    "and pipeline builds off your plate under your agency banner. "
    "No full-time overhead, and your clients never know I existed.\n\n"
    "Got any backlogged backend tasks sitting on your team's plate right "
    "now where an extra senior dev helps?\n\n"
    "Best,\n"
    "Rob Rowan"
)

# A dedicated Jinja2 environment with autoescaping disabled (plain-text
# email bodies, not HTML) and strict undefined variables so a missing
# context key fails loudly rather than silently rendering "None".
_ENV: Environment = Environment(autoescape=False, undefined=StrictUndefined)


@dataclass(frozen=True)
class LeadDraft:
    """A rendered outreach draft for a single lead.

    Attributes:
        subject: The rendered subject line.
        body: The rendered plain-text body.
        first_name: The extracted first name used during rendering.
    """

    subject: str
    body: str
    first_name: str


def extract_first_name(contact_name: str | None) -> str:
    """Extract a usable first name from a contact's full name.

    Args:
        contact_name: The contact's full name (e.g. ``"Jane Doe"``), or
            ``None``/empty when no contact name is on record.

    Returns:
        str: The first whitespace-delimited token of ``contact_name``,
            title-cased.  Returns ``"there"`` as a safe generic
            fallback when no contact name is available.
    """
    if not contact_name or not contact_name.strip():
        return "there"
    first_token = contact_name.strip().split()[0]
    return first_token.strip(",.").title()


def render_draft(
    *,
    company_name: str,
    contact_name: str | None = None,
    tech_stack: str | None = None,
    subject_template: str = DEFAULT_SUBJECT_TEMPLATE,
    body_template: str = DEFAULT_BODY_TEMPLATE,
) -> LeadDraft:
    """Render a personalized subject/body draft for a lead.

    Args:
        company_name: The lead's company name.
        contact_name: The lead's full contact name, or ``None``.
        tech_stack: The lead's recorded technology stack, or ``None``.
        subject_template: The Jinja2 subject template string.  Defaults
            to :data:`DEFAULT_SUBJECT_TEMPLATE`.
        body_template: The Jinja2 body template string.  Defaults to
            :data:`DEFAULT_BODY_TEMPLATE`.

    Returns:
        LeadDraft: The rendered subject, body, and extracted first
            name.
    """
    first_name = extract_first_name(contact_name)
    context = {
        "first_name": first_name,
        "company_name": company_name,
        "tech_stack": tech_stack or "",
    }

    subject = _ENV.from_string(subject_template).render(**context)
    body = _ENV.from_string(body_template).render(**context)

    return LeadDraft(subject=subject, body=body, first_name=first_name)
