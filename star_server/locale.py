from __future__ import annotations

from pathlib import Path
from typing import Any


def normalize_locale_tag(raw_locale: Any) -> str:
    if not isinstance(raw_locale, str):
        return ""
    trimmed = raw_locale.replace("_", "-").strip()
    if not trimmed:
        return ""
    parts = [part for part in trimmed.split("-") if part]
    if not parts:
        return ""

    normalized: list[str] = []
    for index, part in enumerate(parts):
        if index == 0:
            normalized.append(part.lower())
        elif len(part) == 4 and part.isalpha():
            normalized.append(part[0].upper() + part[1:].lower())
        elif len(part) in {2, 3} and part.isalnum():
            normalized.append(part.upper())
        else:
            normalized.append(part)
    return "-".join(normalized)


def parse_primary_accept_language(header_value: str | None) -> str:
    if not header_value:
        return ""
    first_token = header_value.split(",", 1)[0].split(";", 1)[0].strip()
    return normalize_locale_tag(first_token)


def parse_locale_from_form_data(form_data: Any, accept_language_header: str | None) -> str:
    raw_locale = form_data.get("locale")
    if isinstance(raw_locale, str) and raw_locale.strip():
        return normalize_locale_tag(raw_locale) or "en"
    return parse_primary_accept_language(accept_language_header) or "en"


def android_values_directory_to_locale(values_dir_name: str) -> str:
    if values_dir_name == "values":
        return "en"
    if values_dir_name.startswith("values-b+"):
        return normalize_locale_tag(values_dir_name[len("values-b+") :].replace("+", "-")) or "en"
    if values_dir_name.startswith("values-"):
        return normalize_locale_tag(values_dir_name[len("values-") :]) or "en"
    return "en"


def list_localization_paths(root_dir: Path) -> list[Path]:
    if not root_dir.exists():
        return []

    results: list[Path] = []
    for path in root_dir.rglob("celestial_objects.xml"):
        if path.is_file():
            results.append(path.resolve())
    return sorted(results)


def list_available_locales(localization_paths: list[Path]) -> list[str]:
    locales = {
        android_values_directory_to_locale(path.parent.name)
        for path in localization_paths
    }
    return sorted(locales)
