"""End-to-end verification of LLM engine email enrichment integration.

This script verifies that when a lead with Name="Leon Shmueli" and
Domain="leonai.io" is evaluated with a missing/placeholder email, the
LLM engine's enrichment logic sets ``verified_email`` to
``leon@leonai.io`` (the highest-probability candidate) instead of
falling back to ``contact@example.com``.

The Gemini model call is mocked so the test is deterministic and does
not require live GCP credentials.
"""

from __future__ import annotations

from unittest.mock import patch

from llm_engine import LLMEngine, LeadEvaluation


def main() -> None:
    """Run the LLM enrichment integration verification."""
    print("--- LLM Engine Email Enrichment Integration ---")

    # Build a fake LeadEvaluation response.
    fake_evaluation = LeadEvaluation(
        qualification_verdict="QUALIFIED",
        reasoning="Strong backend fit.",
        custom_pitch=(
            "Your agency builds web apps with real backend needs. "
            "That suggests scaling challenges worth addressing. "
            "Let's find 15 minutes to discuss."
        ),
        search_helpers=None,
    )

    # Create a minimal engine without real credentials.
    engine = LLMEngine.__new__(LLMEngine)
    engine._client = None

    # Case 1: verified_email is None -> should enrich to leon@leonai.io.
    captured_prompts: list[str] = []

    def fake_generate(prompt: str) -> object:
        captured_prompts.append(prompt)
        return type(
            "FakeResponse", (), {"text": fake_evaluation.model_dump_json()}
        )()

    with patch.object(LLMEngine, "_generate", side_effect=fake_generate):
        with patch(
            "email_enricher.domain_has_mail_server", return_value=True
        ):
            engine.evaluate_lead(
                company_name="Leon AI",
                website_text="We build AI-powered web applications.",
                domain="leonai.io",
                verified_email=None,
                contact_name="Leon Shmueli",
            )
            assert captured_prompts, "No prompt was captured"
            assert "leon@leonai.io" in captured_prompts[0], (
                f"Enriched email not in prompt: {captured_prompts[0]}"
            )
            assert "contact@example.com" not in captured_prompts[0], (
                "Placeholder email leaked into prompt"
            )
            print(
                "[OK] verified_email=None -> enriched to "
                "leon@leonai.io in prompt"
            )

    # Case 2: verified_email is a placeholder -> should enrich.
    captured_prompts.clear()

    with patch.object(LLMEngine, "_generate", side_effect=fake_generate):
        with patch(
            "email_enricher.domain_has_mail_server", return_value=True
        ):
            engine.evaluate_lead(
                company_name="Leon AI",
                website_text="We build AI-powered web applications.",
                domain="leonai.io",
                verified_email="contact@example.com",
                contact_name="Leon Shmueli",
            )
            assert captured_prompts, "No prompt was captured"
            assert "leon@leonai.io" in captured_prompts[0], (
                f"Placeholder not replaced: {captured_prompts[0]}"
            )
            assert "contact@example.com" not in captured_prompts[0], (
                "Placeholder email leaked into prompt"
            )
            print(
                "[OK] verified_email=contact@example.com -> enriched to "
                "leon@leonai.io in prompt"
            )

    # Case 3: verified_email is already valid -> no enrichment.
    captured_prompts.clear()

    with patch.object(LLMEngine, "_generate", side_effect=fake_generate):
        with patch(
            "email_enricher.domain_has_mail_server", return_value=True
        ):
            engine.evaluate_lead(
                company_name="Leon AI",
                website_text="We build AI-powered web applications.",
                domain="leonai.io",
                verified_email="leon@leonai.io",
                contact_name="Leon Shmueli",
            )
            assert captured_prompts, "No prompt was captured"
            assert "leon@leonai.io" in captured_prompts[0]
            print(
                "[OK] verified_email=leon@leonai.io -> unchanged (no "
                "placeholder)"
            )

    # Case 4: no contact_name -> no enrichment possible.
    captured_prompts.clear()

    with patch.object(LLMEngine, "_generate", side_effect=fake_generate):
        with patch(
            "email_enricher.domain_has_mail_server", return_value=True
        ):
            engine.evaluate_lead(
                company_name="Leon AI",
                website_text="We build AI-powered web applications.",
                domain="leonai.io",
                verified_email=None,
                contact_name=None,
            )
            assert captured_prompts, "No prompt was captured"
            assert "Verified Email: (not available)" in captured_prompts[0], (
                "No contact_name should leave email unavailable"
            )
            print(
                "[OK] no contact_name -> email remains unavailable "
                "(no enrichment)"
            )

    print()
    print("=== ALL LLM ENRICHMENT INTEGRATION CHECKS PASSED ===")


if __name__ == "__main__":
    main()