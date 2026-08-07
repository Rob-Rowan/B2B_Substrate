"""LLM evaluation and pitch engine for B2B Substrate.

This module implements the Vertex AI Gemini 3.6 Flash integration used to
qualify partner agency leads and generate hyper-specific cold email
pitches.  It authenticates using a local GCP service account JSON key
file, enforces structured JSON output through a Pydantic schema, and
applies strict cold-email content rules to every generated pitch.

The module exposes a :class:`LLMEngine` class that wraps the Google GenAI
Python SDK and provides a single ``evaluate_lead`` method used by the
ingestion pipeline and the Streamlit application layer.
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from config import (
    # GITHUB_URL,
    # LINKEDIN_URL,
    CredentialConfig,
    load_config,
)
from email_enricher import (
    generate_email_candidates,
    is_placeholder_email,
)
from sanitizer import sanitize_email_body, sanitize_text

# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------


class LeadEvaluation(BaseModel):
    """Structured output produced by the Gemini qualification engine.

    Attributes:
        qualification_verdict: Either ``QUALIFIED`` or ``DISQUALIFIED``.
        reasoning: A concise explanation of the qualification decision.
        custom_pitch: A hyper-specific, 3-sentence plain-text cold email.
        search_helpers: Optional targeted Google/LinkedIn search strings
            to locate missing domain or email information when those
            fields are absent from the lead.  ``None`` when both fields
            are present.
    """

    qualification_verdict: str = Field(
        description="Either QUALIFIED or DISQUALIFIED.",
    )
    reasoning: str = Field(
        description="A concise explanation of the qualification decision.",
    )
    custom_pitch: str = Field(
        description=(
            "A hyper-specific, 3-sentence plain-text cold email with a "
            "simple text signature."
        ),
    )
    search_helpers: str | None = Field(
        default=None,
        description=(
            "When the lead's verified_email or domain is missing, "
            "these are targeted Google and LinkedIn search strings to "
            "help locate the missing contact information (e.g. "
            "'site:linkedin.com/in \"Company Name\" \"Founder\" OR "
            "\"CEO\"' and 'Company Name official website'). When "
            "both domain and verified_email are present, this is null."
        ),
    )


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT: str = (
    "You are a senior B2B sales engineer evaluating partner agency "
    "websites for backend development outsourcing fit.\n\n"
    "Your task is to determine whether the agency is a strong candidate "
    "for backend engineering services, and if so, generate a cold email "
    "that is hyper-specific to their business.\n\n"
    "QUALIFICATION RULES:\n"
    "- QUALIFIED: The agency builds web applications, mobile apps, or "
    "digital products and shows signs of backend tech debt, legacy "
    "integrations, or scaling needs.\n"
    "- DISQUALIFIED: The agency is a pure design studio, a solo "
    "freelancer with no team, a non-software business, or the website "
    "text is too sparse to evaluate.\n\n"
    "COLD EMAIL RULES:\n"
    "- Write EXACTLY 3 sentences in the body.\n"
    "- The first sentence must reference a specific detail from the "
    "agency's website (a named client, a listed service, a technology, "
    "or a stated problem).\n"
    "- The second sentence must connect that detail to backend tech "
    "debt, integrations, or scaling challenges.\n"
    "- The third sentence must propose a brief, concrete next step.\n"
    "- The signature must be exactly:\n"
    "Rob Rowan\n"
    # f"{GITHUB_URL}\n"
    # f"{LINKEDIN_URL}\n"
    "- Use plain text only. No HTML tags, no markdown, no bullet points, "
    "no emojis, no tracking pixels.\n"
    "- Do not include a subject line.\n"
    "- Do not include any text after the signature.\n\n"
    "DATA AVAILABILITY & ENRICHMENT:\n"
    "- The website_text field may contain either scraped website content "
    "or raw directory metadata (description, address, tiers, slug) when "
    "no website was scraped. Evaluate the lead using whatever text is "
    "available.\n"
    "- The domain and verified_email fields may be missing. When either "
    "is absent, populate the search_helpers field with targeted Google "
    "and LinkedIn search strings to help locate the missing contact "
    "information. When both are present, set search_helpers to null.\n"
    "- Example search_helpers entries:\n"
    "  - site:linkedin.com/in \"[Company Name]\" \"Founder\" OR \"CEO\"\n"
    "  - [Company Name] official website\n"
    "- If neither website_text nor metadata is available, disqualify the "
    "lead as too sparse to evaluate."
)

# ---------------------------------------------------------------------------
# LLM engine
# ---------------------------------------------------------------------------


class LLMEngine:
    """Vertex AI Gemini 3.6 Flash evaluation engine.

    The engine authenticates using a local GCP service account JSON key
    file, sends sanitized agency website text to Gemini 3.6 Flash, and
    parses the structured JSON response into a :class:`LeadEvaluation`.

    Attributes:
        credentials: The resolved GCP credential configuration.
        client: The Google GenAI client instance, or ``None`` when the
            engine has not been initialized.
    """

    def __init__(self, credentials: CredentialConfig | None = None) -> None:
        """Initialize the LLM engine with resolved credentials.

        Args:
            credentials: The resolved GCP credential configuration.  When
                ``None``, the application configuration is loaded and its
                credentials are used.

        Raises:
            RuntimeError: If no GCP service account credentials are
                available.
        """
        config = load_config()
        self.credentials: CredentialConfig = credentials or config.credentials

        if not self.credentials.has_credentials:
            raise RuntimeError(
                "No GCP service account credentials found. Set "
                "GOOGLE_APPLICATION_CREDENTIALS or place a service "
                "account JSON key file in the project root."
            )

        self._client: Any | None = None

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Lazily create and return the Google GenAI client.

        The ``GOOGLE_APPLICATION_CREDENTIALS`` environment variable is
        set to the resolved service account path before the client is
        constructed so that the underlying ``google.auth.default()``
        call picks up the correct credentials.

        Returns:
            Any: The configured Google GenAI client instance.
        """
        if self._client is not None:
            return self._client

        service_account_path = self.credentials.service_account_path
        if service_account_path is None:
            raise RuntimeError("GCP service account path is not resolved.")

        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(
            service_account_path
        )

        from google import genai

        self._client = genai.Client(
            vertexai=True,
            project=self.credentials.gcp_project,
            location=self.credentials.gcp_location,
        )
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_lead(
        self,
        *,
        company_name: str,
        website_text: str,
        domain: str | None = None,
        verified_email: str | None = None,
        **kwargs: Any,
    ) -> LeadEvaluation:
        """Evaluate a lead and generate a custom cold email pitch.

        The website text is sanitized before being sent to the model to
        prevent prompt injection and remove HTML noise.  The model output
        is parsed into a :class:`LeadEvaluation` and the pitch is
        sanitized again to enforce the plain-text cold-email rules.

        When ``domain`` or ``verified_email`` are missing, the model is
        instructed to populate ``search_helpers`` with targeted search
        strings instead.

        When ``verified_email`` is missing or a placeholder and a
        ``contact_name`` is available in ``kwargs``, the email pattern
        generator is invoked to propose the highest-probability candidate
        (e.g. ``{first}@{domain}``) as the default ``verified_email``.

        Args:
            company_name: The agency's company name.
            website_text: The raw scraped website text (may also be raw
                directory metadata such as description or tiers).
            domain: The agency's domain, or ``None`` when not available.
            verified_email: The verified contact email, or ``None`` when
                not available.
            **kwargs: Absorbs any additional metadata keyword arguments
                passed by callers (e.g. ``contact_name``, ``title``,
                ``notes``) so qualification never fails on an unexpected
                keyword argument.

        Returns:
            LeadEvaluation: The structured qualification verdict,
                reasoning, custom pitch, and optional search helpers.

        Raises:
            ValueError: If the website text is empty after sanitization.
            RuntimeError: If the LLM call fails or the response cannot be
                parsed into the expected schema.
        """
        sanitized_text = sanitize_text(website_text)

        if not sanitized_text:
            raise ValueError(
                "Website text is empty after sanitization. "
                "Cannot evaluate the lead."
            )

        # Enrich the verified_email when it is missing or a placeholder.
        contact_name = kwargs.get("contact_name")
        if domain and is_placeholder_email(verified_email) and contact_name:
            candidates = generate_email_candidates(
                str(contact_name), str(domain)
            )
            if candidates:
                verified_email = candidates[0]

        prompt = self._build_prompt(
            company_name=company_name,
            website_text=sanitized_text,
            domain=domain,
            verified_email=verified_email,
        )

        try:
            response = self._generate(prompt)
            return self._parse_response(response)
        except Exception as exc:
            location = domain or "no domain"
            raise RuntimeError(
                f"LLM evaluation failed for {company_name} ({location}): "
                f"{exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate(self, prompt: str) -> Any:
        """Send a prompt to the Gemini model and return the raw response.

        Args:
            prompt: The complete user prompt to send.

        Returns:
            Any: The raw response object from the Gemini client.

        Raises:
            RuntimeError: If the model call fails.
        """
        client = self._get_client()

        from google.genai import types

        try:
            return client.models.generate_content(
                model=self.credentials.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=LeadEvaluation.model_json_schema(),
                ),
            )
        except Exception as exc:
            raise RuntimeError(
                f"Gemini model call failed: {exc}"
            ) from exc

    def _build_prompt(
        self,
        *,
        company_name: str,
        website_text: str,
        domain: str | None = None,
        verified_email: str | None = None,
    ) -> str:
        """Build the full prompt sent to the Gemini model.

        When ``domain`` or ``verified_email`` are ``None``, a contextual
        note is appended so the model knows to populate the
        ``search_helpers`` field instead of leaving it blank.

        Args:
            company_name: The agency's company name.
            website_text: The sanitized website text.
            domain: The agency's domain, or ``None`` when not available.
            verified_email: The verified contact email, or ``None`` when
                not available.

        Returns:
            str: The complete user prompt.
        """
        missing_fields: list[str] = []
        if not domain:
            missing_fields.append("domain")
        if not verified_email:
            missing_fields.append("verified_email")

        enrichment_note = (
            "\n\nThe lead is missing the following fields: "
            + ", ".join(missing_fields)
            + ". Per the DATA AVAILABILITY & ENRICHMENT instructions "
            "above, populate search_helpers with targeted Google and "
            "LinkedIn search strings to locate this information. When "
            "both fields are present, set search_helpers to null."
            if missing_fields
            else ""
        )

        return (
            f"Company: {company_name}\n"
            f"Domain: {domain or '(not available)'}\n"
            f"Verified Email: {verified_email or '(not available)'}\n\n"
            f"Website text:\n{website_text}\n\n"
            "Evaluate this agency and generate the cold email pitch "
            "according to the rules above."
            + enrichment_note
        )

    def _parse_response(self, response: Any) -> LeadEvaluation:
        """Parse the raw Gemini response into a LeadEvaluation.

        Args:
            response: The raw response object from the Gemini client.

        Returns:
            LeadEvaluation: The parsed structured output.

        Raises:
            RuntimeError: If the response text is missing or cannot be
                parsed into the expected schema.
        """
        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("Gemini returned an empty response.")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Gemini returned invalid JSON: {exc}"
            ) from exc

        try:
            evaluation = LeadEvaluation.model_validate(data)
        except ValidationError as exc:
            raise RuntimeError(
                f"Gemini response failed schema validation: {exc}"
            ) from exc

        # Enforce the cold-email content rules on the generated pitch.
        evaluation.custom_pitch = sanitize_email_body(
            evaluation.custom_pitch
        )

        return evaluation


def evaluate_lead(
    *,
    company_name: str,
    website_text: str,
    domain: str | None = None,
    verified_email: str | None = None,
    credentials: CredentialConfig | None = None,
    **kwargs: Any,
) -> LeadEvaluation:
    """Convenience wrapper that evaluates a lead with a fresh engine.

    Args:
        company_name: The agency's company name.
        website_text: The raw scraped website text (may also be raw
            directory metadata such as description or tiers).
        domain: The agency's domain, or ``None`` when not available.
        verified_email: The verified contact email, or ``None`` when
            not available.
        credentials: Optional resolved GCP credential configuration.
        **kwargs: Absorbs any additional metadata keyword arguments
            passed by callers so qualification never fails on an
            unexpected keyword argument.

    Returns:
        LeadEvaluation: The parsed structured output.

    Raises:
        RuntimeError: If the LLM call fails or credentials are missing.
    """
    engine = LLMEngine(credentials=credentials)
    return engine.evaluate_lead(
        company_name=company_name,
        website_text=website_text,
        domain=domain,
        verified_email=verified_email,
        **kwargs,
    )
