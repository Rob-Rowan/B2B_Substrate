"""Verification script for deep SMTP mailbox verification.

This script tests the ``verify_mailbox`` function against a known real
email and a fake address to confirm bad emails are caught before they
enter the send queue.  It prints results to stdout and exits with a
non-zero code if any assertion fails.

The MX lookup and SMTP connection are mocked for deterministic testing
of the classification logic, and a live DNS check is also attempted.
"""

from __future__ import annotations

from unittest.mock import patch

from verifier import verify_mailbox


def main() -> None:
    """Run all mailbox verification checks and print results."""
    print("--- Deep SMTP Mailbox Verification ---")

    # 1. Invalid email format -> INVALID_USER.
    result = verify_mailbox("not-an-email")
    assert result["status"] == "INVALID_USER", (
        f"Expected INVALID_USER, got {result['status']}"
    )
    print("[OK] verify_mailbox: invalid format -> INVALID_USER")

    # 2. Domain with no MX records -> UNKNOWN_UNVERIFIED.
    with patch("verifier.lookup_mx_records", return_value=()):
        result = verify_mailbox("user@nodomain.invalid")
        assert result["status"] == "UNKNOWN_UNVERIFIED", (
            f"Expected UNKNOWN_UNVERIFIED, got {result['status']}"
        )
        assert result["mx_host"] is None
    print("[OK] verify_mailbox: no MX records -> UNKNOWN_UNVERIFIED")

    # 3. SMTP connection failure -> UNKNOWN_UNVERIFIED.
    with patch("verifier.lookup_mx_records", return_value=("mx1.test.com",)):
        with patch(
            "verifier.smtplib.SMTP",
            side_effect=ConnectionError("Connection refused"),
        ):
            result = verify_mailbox("user@test.com")
            assert result["status"] == "UNKNOWN_UNVERIFIED", (
                f"Expected UNKNOWN_UNVERIFIED, got {result['status']}"
            )
            assert result["mx_host"] == "mx1.test.com"
    print("[OK] verify_mailbox: SMTP connection failure -> UNKNOWN_UNVERIFIED")

    # 4. RCPT TO 250 + NOT catch-all -> VERIFIED_DELIVERABLE.
    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def ehlo(self):
            return 250, b"OK"

        def mail(self, sender):
            return 250, b"OK"

        def rcpt(self, recipient):
            if recipient == "random_test_xyz123@test.com":
                return 550, b"User unknown"
            return 250, b"OK"

    with patch("verifier.lookup_mx_records", return_value=("mx1.test.com",)):
        with patch("verifier.smtplib.SMTP", FakeSMTP):
            result = verify_mailbox("real.user@test.com")
            assert result["status"] == "VERIFIED_DELIVERABLE", (
                f"Expected VERIFIED_DELIVERABLE, got {result['status']}"
            )
            assert result["is_catchall"] is False
    print("[OK] verify_mailbox: RCPT 250 + not catch-all -> VERIFIED_DELIVERABLE")

    # 5. RCPT TO 250 + IS catch-all -> RISKY_CATCHALL.
    class FakeCatchallSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def ehlo(self):
            return 250, b"OK"

        def mail(self, sender):
            return 250, b"OK"

        def rcpt(self, recipient):
            return 250, b"OK"

    with patch("verifier.lookup_mx_records", return_value=("mx1.test.com",)):
        with patch("verifier.smtplib.SMTP", FakeCatchallSMTP):
            result = verify_mailbox("any.user@test.com")
            assert result["status"] == "RISKY_CATCHALL", (
                f"Expected RISKY_CATCHALL, got {result['status']}"
            )
            assert result["is_catchall"] is True
    print("[OK] verify_mailbox: RCPT 250 + catch-all -> RISKY_CATCHALL")

    # 6. RCPT TO 550 -> INVALID_USER.
    class FakeInvalidSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def ehlo(self):
            return 250, b"OK"

        def mail(self, sender):
            return 250, b"OK"

        def rcpt(self, recipient):
            return 550, b"User unknown"

    with patch("verifier.lookup_mx_records", return_value=("mx1.test.com",)):
        with patch("verifier.smtplib.SMTP", FakeInvalidSMTP):
            result = verify_mailbox("fake.user@test.com")
            assert result["status"] == "INVALID_USER", (
                f"Expected INVALID_USER, got {result['status']}"
            )
    print("[OK] verify_mailbox: RCPT 550 -> INVALID_USER")

    # 7. RCPT TO 551 -> INVALID_USER.
    class FakeInvalid551SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def ehlo(self):
            return 250, b"OK"

        def mail(self, sender):
            return 250, b"OK"

        def rcpt(self, recipient):
            return 551, b"User not local"

    with patch("verifier.lookup_mx_records", return_value=("mx1.test.com",)):
        with patch("verifier.smtplib.SMTP", FakeInvalid551SMTP):
            result = verify_mailbox("fake.user@test.com")
            assert result["status"] == "INVALID_USER", (
                f"Expected INVALID_USER, got {result['status']}"
            )
    print("[OK] verify_mailbox: RCPT 551 -> INVALID_USER")

    # 8. MAIL FROM rejected -> UNKNOWN_UNVERIFIED.
    class FakeMailRejectSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def ehlo(self):
            return 250, b"OK"

        def mail(self, sender):
            return 550, b"Sender rejected"

        def rcpt(self, recipient):
            return 250, b"OK"

    with patch("verifier.lookup_mx_records", return_value=("mx1.test.com",)):
        with patch("verifier.smtplib.SMTP", FakeMailRejectSMTP):
            result = verify_mailbox("user@test.com")
            assert result["status"] == "UNKNOWN_UNVERIFIED", (
                f"Expected UNKNOWN_UNVERIFIED, got {result['status']}"
            )
    print("[OK] verify_mailbox: MAIL FROM rejected -> UNKNOWN_UNVERIFIED")

    # 10. SMTP Timeout -> UNVERIFIED_TIMEOUT.
    with patch("verifier.lookup_mx_records", return_value=("mx1.test.com",)):
        with patch(
            "verifier.smtplib.SMTP",
            side_effect=TimeoutError("Connection timed out"),
        ):
            result = verify_mailbox("user@test.com")
            assert result["status"] == "UNVERIFIED_TIMEOUT", (
                f"Expected UNVERIFIED_TIMEOUT, got {result['status']}"
            )
            assert result["mx_host"] == "mx1.test.com"
    print("[OK] verify_mailbox: SMTP Timeout -> UNVERIFIED_TIMEOUT")

    # 11. OSError with 'timed out' -> UNVERIFIED_TIMEOUT.
    with patch("verifier.lookup_mx_records", return_value=("mx1.test.com",)):
        with patch(
            "verifier.smtplib.SMTP",
            side_effect=OSError("connection timed out"),
        ):
            result = verify_mailbox("user@test.com")
            assert result["status"] == "UNVERIFIED_TIMEOUT", (
                f"Expected UNVERIFIED_TIMEOUT, got {result['status']}"
            )
            assert result["mx_host"] == "mx1.test.com"
    print("[OK] verify_mailbox: OSError 'timed out' -> UNVERIFIED_TIMEOUT")

    # 12. Stagger delay: consecutive checks against the same domain are delayed.
    import time
    with patch("verifier.lookup_mx_records", return_value=("mx1.test.com",)):
        with patch("verifier.smtplib.SMTP", FakeSMTP):
            # First verification
            verify_mailbox("user1@test.com")

            # Second verification of same domain. Let's patch time.sleep to trace the sleep call.
            with patch("verifier.time.sleep") as mock_sleep:
                verify_mailbox("user2@test.com")
                assert mock_sleep.called, "Expected time.sleep to be called to stagger the pings"
                args, _ = mock_sleep.call_args
                sleep_time = args[0]
                assert 1.0 <= sleep_time <= 2.0, f"Expected sleep time to be between 1.0 and 2.0, got {sleep_time}"
    print("[OK] verify_mailbox: Stagger delay enforced between same domain checks")

    # 9. Live DNS MX check for gmail.com (best-effort, not fatal).
    try:
        from verifier import lookup_mx_records

        mx = lookup_mx_records("gmail.com")
        print(
            f"[INFO] Live DNS MX check for gmail.com: "
            f"{len(mx)} MX records found"
        )
    except Exception as exc:
        print(f"[INFO] Live DNS MX check skipped: {exc}")

    print()
    print("=== ALL MAILBOX VERIFICATION CHECKS PASSED ===")


if __name__ == "__main__":
    main()