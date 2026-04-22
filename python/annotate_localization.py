#!/usr/bin/env python3
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

from annotate_types import LocalizationBundle


RESOURCE_KEY_PREFIXES = ("the_", "great_")
RESOURCE_KEY_SUFFIXES = (
    "_globular_cluster",
    "_open_cluster",
    "_supernova_remnant",
    "_planetary_nebula",
    "_star_cluster",
    "_galaxy_cluster",
    "_cluster",
    "_nebula",
    "_galaxy",
    "_remnant",
)
CONSTELLATION_RESOURCE_OVERRIDES = {
    "Ser": ("serpens_caput", "serpens_cauda"),
}
SUPPLEMENTAL_CONSTELLATION_ABBR_OVERRIDES = {
    "serpens_caput": "Ser",
    "serpens_cauda": "Ser",
}


def strip_diacritics(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(character for character in normalized if not unicodedata.combining(character))


def normalize_constellation_key(value: str | None) -> str:
    text = strip_diacritics((value or "").strip().lower())
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if normalized:
        return normalized
    return re.sub(r"\s+", "", (value or "").strip().lower())


def normalize_lookup_key(value: str | None) -> str:
    return normalize_constellation_key(value).replace("_", "")


def normalize_human_alias(value: str | None) -> str | None:
    text = (value or "").replace("_", " ").strip()
    if not text:
        return None
    return re.sub(r"\s+", " ", text)


def resource_key_candidates(*values: str | None) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()

    def append_candidate(candidate: str | None) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            results.append(candidate)

    queue: list[str] = []
    queued: set[str] = set()

    def enqueue(candidate: str | None) -> None:
        if candidate and candidate not in queued:
            queued.add(candidate)
            queue.append(candidate)

    for value in values:
        enqueue(normalize_constellation_key(value))

    while queue:
        candidate = queue.pop(0)
        append_candidate(candidate)
        for prefix in RESOURCE_KEY_PREFIXES:
            if candidate.startswith(prefix):
                enqueue(candidate[len(prefix) :])
        for suffix in RESOURCE_KEY_SUFFIXES:
            if candidate.endswith(suffix) and len(candidate) > len(suffix):
                enqueue(candidate[: -len(suffix)])

    return results


def canonicalize_locale_tag(value: str | None) -> str:
    text = (value or "").replace("_", "-").strip()
    if not text:
        return "en"
    parts = [part for part in text.split("-") if part]
    if not parts:
        return "en"

    normalized: list[str] = []
    for index, part in enumerate(parts):
        if index == 0:
            normalized.append(part.lower())
        elif len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        elif len(part) in {2, 3} and part.isalnum():
            normalized.append(part.upper())
        else:
            normalized.append(part)
    return "-".join(normalized)


def locale_candidates(requested_locale: str) -> list[str]:
    requested = canonicalize_locale_tag(requested_locale)
    candidates: list[str] = []

    def append_candidate(candidate: str | None) -> None:
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    append_candidate(requested)

    parts = requested.split("-")
    if parts and parts[0] == "zh":
        regions = {part for part in parts[1:] if len(part) in {2, 3} and part.isupper()}
        if "Hans" in parts or regions.intersection({"CN", "SG", "MY"}):
            append_candidate("zh-Hans")
        if "Hant" in parts or regions.intersection({"TW", "HK", "MO"}):
            append_candidate("zh-Hant")

    while len(parts) > 1:
        parts = parts[:-1]
        append_candidate("-".join(parts))

    append_candidate("en")
    return candidates


def android_values_directory_to_locale(values_dir_name: str) -> str:
    if values_dir_name == "values":
        return "en"
    if values_dir_name.startswith("values-b+"):
        return canonicalize_locale_tag(values_dir_name[len("values-b+") :].replace("+", "-"))
    if values_dir_name.startswith("values-"):
        return canonicalize_locale_tag(values_dir_name[len("values-") :])
    return "en"


def load_localized_names(localization_paths: list[Path], locale: str | None = None) -> LocalizationBundle:
    localized_catalogs: dict[str, dict[str, str]] = {}
    for localization_path in localization_paths:
        if not localization_path.exists():
            continue
        locale_tag = android_values_directory_to_locale(localization_path.parent.name)
        localized_names = localized_catalogs.setdefault(locale_tag, {})
        root = ET.parse(localization_path).getroot()
        for node in root.findall("./string"):
            key = normalize_constellation_key(node.attrib.get("name"))
            value = "".join(node.itertext()).strip()
            if key and value and key not in localized_names:
                localized_names[key] = value

    available_locales = sorted(localized_catalogs)
    resolved_locale = "en" if "en" in localized_catalogs else (available_locales[0] if available_locales else "en")
    strings = dict(localized_catalogs.get("en", {}))
    requested_locale = canonicalize_locale_tag(locale)

    for candidate in locale_candidates(requested_locale):
        if candidate not in localized_catalogs:
            continue
        resolved_locale = candidate
        strings = dict(localized_catalogs.get("en", {}))
        strings.update(localized_catalogs[candidate])
        break

    return LocalizationBundle(
        requested_locale=requested_locale,
        resolved_locale=resolved_locale,
        available_locales=available_locales,
        strings=strings,
    )


def resolve_localized_name(localized_names: dict[str, str], *values: str | None) -> str | None:
    for key in resource_key_candidates(*values):
        translated = localized_names.get(key)
        if translated:
            return translated.strip()
    return None


def find_resource_key(localized_names: dict[str, str], *values: str | None) -> str | None:
    for key in resource_key_candidates(*values):
        if key in localized_names:
            return key
    return None


def resolve_constellation_display_name(
    abbr: str,
    english_name: str,
    native_name: str | None,
    localized_names: dict[str, str],
) -> str:
    overrides = CONSTELLATION_RESOURCE_OVERRIDES.get(abbr, ())
    translated = resolve_localized_name(localized_names, native_name, english_name, *overrides)
    if translated:
        if abbr == "Ser":
            return re.sub(r"[（(].*?[）)]", "", translated).strip()
        return translated
    return native_name or english_name or abbr
