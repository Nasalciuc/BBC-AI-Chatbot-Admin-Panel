"""PR-5 — mask_pii hardened for the cases the 3 Nov reading found leaking."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.privacy.mask import mask_pii


def test_email_and_phone():
    out = mask_pii("Call +1 (646) 555-0199 or email john.doe@gmail.com")
    assert "555-0199" not in out and "gmail.com" not in out
    assert "[phone]" in out and "[email]" in out


def test_partial_phone():
    # >=4 consecutive digits next to +/(/- — the partial the old masker left.
    out = mask_pii("my number starts +1 (6465")
    assert "6465" not in out and "[phone]" in out


def test_pnr_reference():
    assert "ABC123" not in mask_pii("Your booking reference is ABC123")
    # a plain English word of 6 letters is not a PNR
    assert "PLEASE" in mask_pii("PLEASE confirm")


def test_exact_date():
    assert "2026-06-15" not in mask_pii("Departing 2026-06-15")


def test_first_name_after_greeting():
    out = mask_pii("Welcome, Priya! Where are you flying?")
    assert "Priya" not in out and "[name]" in out


def test_supplied_names():
    out = mask_pii("Thanks Oleksandr, I will call Maria back", names=["Oleksandr Petrenko", "Maria"])
    assert "Oleksandr" not in out and "Maria" not in out


def test_none_is_safe():
    assert mask_pii(None) == ""
