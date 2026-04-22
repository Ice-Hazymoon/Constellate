from __future__ import annotations

from star_server.locale import normalize_locale_tag, parse_locale_from_form_data, parse_primary_accept_language


def test_normalize_locale_tag_matches_expected_shape() -> None:
    assert normalize_locale_tag("ZH_hant_tw") == "zh-Hant-TW"
    assert normalize_locale_tag(" en ") == "en"
    assert normalize_locale_tag(None) == ""


def test_accept_language_uses_primary_token_only() -> None:
    assert parse_primary_accept_language("ja-JP, en-US;q=0.8") == "ja-JP"
    assert parse_primary_accept_language(None) == ""


def test_form_locale_takes_priority_over_accept_language() -> None:
    form = {"locale": "fr_ca"}
    assert parse_locale_from_form_data(form, "ja-JP, en-US;q=0.8") == "fr-CA"
    assert parse_locale_from_form_data({}, "ja-JP, en-US;q=0.8") == "ja-JP"
    assert parse_locale_from_form_data({}, None) == "en"
