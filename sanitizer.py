"""Input sanitization layer for B2B Substrate.

This module provides pure-Python utilities to clean raw scraped web and
directory data before it is stored in the database or forwarded to the
Gemini LLM engine.

The sanitizer performs the following operations:

1. Strips embedded ``<script>`` and ``<style>`` blocks.
2. Removes all remaining HTML tags.
3. Decodes common HTML entities.
4. Removes control characters and non-printable Unicode.
5. Collapses excessive whitespace.
6. Detects and removes known LLM prompt injection signatures.
"""

from __future__ import annotations

import html
import re
from typing import Final

from config import PROMPT_INJECTION_SIGNATURES

# ---------------------------------------------------------------------------
# Regular expression patterns
# ---------------------------------------------------------------------------

# Matches an entire <script>...</script> block, case-insensitively.
_SCRIPT_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL
)

# Matches an entire <style>...</style> block, case-insensitively.
_STYLE_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL
)

# Matches any remaining HTML tag, including self-closing tags.
_HTML_TAG_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")

# Matches HTML comments.
_HTML_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"<!--.*?-->", re.DOTALL
)

# Matches control characters and other non-printable Unicode code points.
_CONTROL_CHAR_RE: Final[re.Pattern[str]] = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
)

# Matches two or more consecutive whitespace characters.
_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")

# Matches a single newline for line-preserving collapse.
_NEWLINE_RE: Final[re.Pattern[str]] = re.compile(r"\n\s*\n+")

# ---------------------------------------------------------------------------
# Public sanitization functions
# ---------------------------------------------------------------------------


def strip_html_tags(raw_text: str) -> str:
    """Remove script blocks, style blocks, comments, and HTML tags.

    Args:
        raw_text: The raw scraped text that may contain HTML markup.

    Returns:
        str: The text with all HTML markup removed.
    """
    if not raw_text:
        return ""

    text = _SCRIPT_BLOCK_RE.sub(" ", raw_text)
    text = _STYLE_BLOCK_RE.sub(" ", text)
    text = _HTML_COMMENT_RE.sub(" ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    return text


def decode_html_entities(text: str) -> str:
    """Decode HTML entities such as ``&`` and ``&#39;``.

    Args:
        text: The text to decode.

    Returns:
        str: The text with HTML entities converted to their Unicode
            equivalents.
    """
    if not text:
        return ""
    return html.unescape(text)


def remove_control_characters(text: str) -> str:
    """Remove control characters and non-printable Unicode code points.

    Args:
        text: The text to clean.

    Returns:
        str: The text with control characters removed.
    """
    if not text:
        return ""
    return _CONTROL_CHAR_RE.sub("", text)


def collapse_whitespace(text: str) -> str:
    """Collapse runs of whitespace into single spaces.

    Consecutive blank lines are reduced to a single newline, and all other
    whitespace runs are collapsed to a single space.

    Args:
        text: The text to normalize.

    Returns:
        str: The whitespace-normalized text.
    """
    if not text:
        return ""
    text = _NEWLINE_RE.sub("\n", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def strip_prompt_injection(text: str) -> str:
    """Remove known LLM prompt injection signatures from text.

    Each signature is matched case-insensitively as a whole phrase.  When a
    signature is found, the offending phrase is removed from the text.

    Args:
        text: The text to scan for injection signatures.

    Returns:
        str: The text with injection signatures removed.
    """
    if not text:
        return ""

    cleaned = text
    for signature in PROMPT_INJECTION_SIGNATURES:
        pattern = re.compile(
            re.escape(signature), re.IGNORECASE
        )
        cleaned = pattern.sub("", cleaned)
    return cleaned


def sanitize_text(raw_text: str | None) -> str:
    """Sanitize raw scraped text for storage and LLM consumption.

    The full pipeline is applied in order:

    1. Strip HTML tags, scripts, styles, and comments.
    2. Decode HTML entities.
    3. Remove control characters.
    4. Strip prompt injection signatures.
    5. Collapse whitespace.

    Args:
        raw_text: The raw scraped text, or ``None``.

    Returns:
        str: The fully sanitized text.  Returns an empty string when the
            input is ``None`` or empty.
    """
    if not raw_text:
        return ""

    text = strip_html_tags(raw_text)
    text = decode_html_entities(text)
    text = remove_control_characters(text)
    text = strip_prompt_injection(text)
    text = collapse_whitespace(text)
    return text


def sanitize_email_body(body: str) -> str:
    """Sanitize a generated email body before it is stored or sent.

    This function enforces the cold-email content rules:

    * Removes any HTML tags.
    * Removes markdown formatting characters.
    * Removes tracking-pixel style markup.
    * Collapses whitespace.

    Args:
        body: The generated email body text.

    Returns:
        str: The sanitized plain-text email body.
    """
    if not body:
        return ""

    text = strip_html_tags(body)
    text = decode_html_entities(text)
    text = remove_control_characters(text)

    # Remove markdown emphasis markers and code fences.
    text = text.replace("**", "").replace("__", "")
    text = text.replace("```", "").replace("`", "")
    text = text.replace("*", "").replace("_", "")

    # Remove tracking-pixel style markup.
    text = text.replace("<img", "").replace("</img>", "")
    text = text.replace("http://", "http://").replace("https://", "https://")

    text = collapse_whitespace(text)
    return text


def contains_injection_signature(text: str) -> bool:
    """Return ``True`` when the text contains a known injection signature.

    Args:
        text: The text to inspect.

    Returns:
        bool: ``True`` when at least one prompt injection signature is
            present in the text.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(signature in lowered for signature in PROMPT_INJECTION_SIGNATURES)