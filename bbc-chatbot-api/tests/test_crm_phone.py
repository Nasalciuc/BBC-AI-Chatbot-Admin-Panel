"""Tests for CRM phone E.164 sanitization."""

from app.services.crm import format_phone_international


def test_format_phone_international_broken_us_formats():
    assert format_phone_international("1+2108844081") == "+12108844081"
    assert format_phone_international("+1+2108844081") == "+12108844081"
    assert format_phone_international("+1 (210) 884-4081") == "+12108844081"
    assert format_phone_international("2108844081") == "+12108844081"


def test_format_phone_international_valid_formats():
    assert format_phone_international("+14254658001") == "+14254658001"
    assert format_phone_international("+442079460958") == "+442079460958"
    assert format_phone_international("0044207946") == "+44207946"
    assert format_phone_international("13125327819") == "+13125327819"
    assert format_phone_international("+1 412 979 5509") == "+14129795509"


def test_format_phone_international_invalid():
    assert format_phone_international("") == ""
    assert format_phone_international("abc") == ""
    assert format_phone_international("123") == ""
