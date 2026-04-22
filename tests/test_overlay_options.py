from __future__ import annotations

from annotate_options import clone_overlay_options, normalize_overlay_options


def test_clone_overlay_options_returns_isolated_copy() -> None:
    options = clone_overlay_options()
    options["layers"]["star_labels"] = False
    assert clone_overlay_options()["layers"]["star_labels"] is True


def test_preset_is_applied_before_overrides() -> None:
    options = normalize_overlay_options(
        {
            "preset": "balanced",
            "detail": {
                "star_label_limit": 22,
            },
        }
    )
    assert options["preset"] == "balanced"
    assert options["detail"]["star_label_limit"] == 22
    assert options["detail"]["include_catalog_dsos"] is False


def test_invalid_preset_falls_back_to_default() -> None:
    options = normalize_overlay_options({"preset": "minimal"})
    assert options["preset"] == "max"
    assert options["detail"]["include_catalog_dsos"] is True


def test_overlay_numbers_and_booleans_are_normalized() -> None:
    options = normalize_overlay_options(
        {
            "detail": {
                "star_label_limit": 999,
                "dso_spacing_scale": -1,
            },
            "layers": {
                "star_labels": 0,
            },
            "mask_foreground": None,
        }
    )
    assert options["detail"]["star_label_limit"] == 80
    assert options["detail"]["dso_spacing_scale"] == 0.1
    assert options["layers"]["star_labels"] is False
    assert options["mask_foreground"] is True
