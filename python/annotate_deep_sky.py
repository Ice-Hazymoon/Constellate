#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord

from annotate_localization import (
    find_resource_key,
    normalize_constellation_key,
    normalize_human_alias,
    normalize_lookup_key,
    resolve_localized_name,
)


def parse_optional_float(value: str | None) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def format_messier_label(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return f"M{int(text)}"
    except ValueError:
        normalized = text.lstrip("0") or text
        return f"M{normalized}"


def choose_common_name(names: list[str]) -> str | None:
    cleaned = [name.strip() for name in names if name and name.strip()]
    if not cleaned:
        return None
    for candidate in cleaned:
        if any(character.isupper() for character in candidate):
            return candidate
    for candidate in cleaned:
        if " " in candidate:
            return candidate
    for candidate in cleaned:
        if any(character.isalpha() for character in candidate):
            return candidate
    return cleaned[0]


def dedupe_aliases(names: list[str]) -> list[str]:
    unique_names: list[str] = []
    seen: set[str] = set()
    for name in names:
        candidate = name.strip()
        key = candidate.casefold()
        if candidate and key not in seen:
            seen.add(key)
            unique_names.append(candidate)
    return unique_names


def strip_catalog_prefix(label: str, *prefixes: str | None) -> str:
    resolved_label = label.strip()
    for prefix in prefixes:
        prefix_text = (prefix or "").strip()
        if not prefix_text:
            continue
        stripped = re.sub(
            rf"^{re.escape(prefix_text)}(?:\s+|[：:：-]\s*)?",
            "",
            resolved_label,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        if stripped and stripped != resolved_label:
            return stripped
    return resolved_label


def resolve_dso_label(
    name: str,
    messier: str | None,
    common_names: list[str],
    localized_names: dict[str, str],
    catalog_id: str | None = None,
) -> str:
    translated = resolve_localized_name(localized_names, messier, *common_names, name, catalog_id)
    if translated:
        return strip_catalog_prefix(translated, messier, catalog_id)
    if messier:
        return messier
    common_name = choose_common_name(common_names)
    if common_name:
        return common_name
    return name


def resolve_dso_resource_key(
    name: str,
    messier: str | None,
    common_names: list[str],
    localized_names: dict[str, str],
    catalog_id: str | None = None,
) -> str | None:
    return find_resource_key(localized_names, messier, *common_names, name, catalog_id)


def normalize_constellation_abbr(value: str | None, constellation_name_map: dict[str, str]) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return constellation_name_map.get(normalize_constellation_key(text), text)


def build_dso_key(
    name: str,
    messier: str | None,
    common_names: list[str],
    catalog_id: str | None = None,
) -> str:
    if messier:
        return normalize_lookup_key(messier)
    if catalog_id:
        return normalize_lookup_key(catalog_id)
    if name:
        return normalize_lookup_key(name)
    common_name = choose_common_name(common_names)
    return normalize_lookup_key(common_name)


def merge_dso_entry(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    target["common_names"] = dedupe_aliases([*target.get("common_names", []), *incoming.get("common_names", [])])
    if incoming.get("curated"):
        target["curated"] = True

    for key in ("type", "const", "messier", "catalog_id"):
        if not target.get(key) and incoming.get(key):
            target[key] = incoming[key]

    for key in ("ra_degrees", "dec_degrees", "magnitude"):
        if target.get(key) is None and incoming.get(key) is not None:
            target[key] = incoming[key]

    if incoming.get("major_axis_arcmin") is not None:
        target["major_axis_arcmin"] = max(target.get("major_axis_arcmin") or 0.0, incoming["major_axis_arcmin"])

    current_common_name = target.get("common_name")
    incoming_common_name = incoming.get("common_name")
    if not current_common_name and incoming_common_name:
        target["common_name"] = incoming_common_name

    current_label = target.get("label")
    incoming_label = incoming.get("label")
    if incoming_label and (not current_label or current_label == target.get("name") or incoming.get("curated")):
        target["label"] = incoming_label
        if incoming.get("label_key"):
            target["label_key"] = incoming["label_key"]
    elif not target.get("label_key") and incoming.get("label_key"):
        target["label_key"] = incoming["label_key"]


def load_openngc_objects(
    dso_path: Path,
    constellation_name_map: dict[str, str],
    localized_names: dict[str, str],
) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    with dso_path.open(encoding="utf-8", errors="ignore") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            name = (row.get("Name") or "").strip()
            ra_text = (row.get("RA") or "").strip()
            dec_text = (row.get("Dec") or "").strip()
            if not name or not ra_text or not dec_text:
                continue
            try:
                coord = SkyCoord(ra_text, dec_text, unit=(u.hourangle, u.deg))
            except ValueError:
                continue

            common_names = dedupe_aliases([item.strip() for item in (row.get("Common names") or "").split(",") if item.strip()])
            messier = format_messier_label(row.get("M"))
            catalog_id = name if name.upper().startswith(("NGC", "IC", "SH2", "B", "C")) else None
            objects.append(
                {
                    "name": name,
                    "type": (row.get("Type") or "").strip(),
                    "const": normalize_constellation_abbr(row.get("Const"), constellation_name_map),
                    "ra_degrees": float(coord.ra.deg),
                    "dec_degrees": float(coord.dec.deg),
                    "major_axis_arcmin": parse_optional_float(row.get("MajAx")),
                    "magnitude": parse_optional_float(row.get("V-Mag")) or parse_optional_float(row.get("B-Mag")),
                    "messier": messier,
                    "catalog_id": catalog_id,
                    "common_name": choose_common_name(common_names),
                    "common_names": common_names,
                    "label": resolve_dso_label(name, messier, common_names, localized_names, catalog_id),
                    "label_key": resolve_dso_resource_key(name, messier, common_names, localized_names, catalog_id),
                    "curated": False,
                }
            )
    return objects


def load_stardroid_dso_objects(
    dso_path: Path,
    constellation_name_map: dict[str, str],
    localized_names: dict[str, str],
) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    with dso_path.open(encoding="utf-8", errors="ignore") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header:
            return []
        for row in reader:
            if not row:
                continue
            if len(row) < 10:
                continue
            if len(row) > 10:
                row = [*row[:9], ",".join(part.strip() for part in row[9:] if part.strip())]

            aliases = [normalize_human_alias(value) for value in row[0].split("|")]
            aliases = [alias for alias in aliases if alias]
            if not aliases:
                continue

            primary_name = aliases[0]
            common_name = normalize_human_alias(row[9])
            common_names = dedupe_aliases([*aliases[1:], *([common_name] if common_name else [])])

            try:
                ra_degrees = float(row[2]) * 15.0
                dec_degrees = float(row[3])
            except ValueError:
                continue

            messier = None
            if primary_name.upper().startswith("M") and primary_name[1:].replace(".", "", 1).isdigit():
                messier = format_messier_label(primary_name[1:])

            catalog_id = normalize_human_alias(row[6])
            objects.append(
                {
                    "name": primary_name,
                    "type": normalize_human_alias(row[1]) or "",
                    "const": normalize_constellation_abbr(row[7], constellation_name_map),
                    "ra_degrees": ra_degrees,
                    "dec_degrees": dec_degrees,
                    "major_axis_arcmin": parse_optional_float(row[5]),
                    "magnitude": parse_optional_float(row[4]),
                    "messier": messier,
                    "catalog_id": catalog_id,
                    "common_name": choose_common_name(common_names),
                    "common_names": common_names,
                    "label": resolve_dso_label(primary_name, messier, common_names, localized_names, catalog_id),
                    "label_key": resolve_dso_resource_key(primary_name, messier, common_names, localized_names, catalog_id),
                    "curated": True,
                }
            )
    return objects


def load_supplemental_deep_sky_objects(
    supplemental_dso_path: Path | None,
    constellation_name_map: dict[str, str],
    localized_names: dict[str, str],
) -> list[dict[str, Any]]:
    if supplemental_dso_path is None or not supplemental_dso_path.exists():
        return []

    items = json.loads(supplemental_dso_path.read_text(encoding="utf-8"))
    objects: list[dict[str, Any]] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        ra_text = str(item.get("ra") or "").strip()
        dec_text = str(item.get("dec") or "").strip()
        if not name or not ra_text or not dec_text:
            continue

        coord = SkyCoord(ra_text, dec_text, unit=(u.hourangle, u.deg))
        common_names = dedupe_aliases([str(value).strip() for value in item.get("common_names", []) if str(value).strip()])
        common_name = choose_common_name(common_names)
        messier = format_messier_label(item.get("messier"))
        catalog_id = str(item.get("catalog_id") or name).strip() or name
        explicit_label_key = str(item.get("label_key") or "").strip() or None
        label = resolve_localized_name(
            localized_names,
            explicit_label_key,
            messier,
            *common_names,
            name,
            catalog_id,
        )
        label_key = find_resource_key(
            localized_names,
            explicit_label_key,
            messier,
            *common_names,
            name,
            catalog_id,
        )
        objects.append(
            {
                "name": name,
                "type": str(item.get("type") or "").strip(),
                "const": normalize_constellation_abbr(item.get("const"), constellation_name_map),
                "ra_degrees": float(coord.ra.deg),
                "dec_degrees": float(coord.dec.deg),
                "major_axis_arcmin": parse_optional_float(str(item.get("major_axis_arcmin") or "")),
                "magnitude": parse_optional_float(str(item.get("magnitude") or "")),
                "messier": messier,
                "catalog_id": catalog_id,
                "common_name": common_name,
                "common_names": common_names,
                "label": strip_catalog_prefix(label, messier, catalog_id) if label else (common_name or name),
                "label_key": label_key,
                "curated": True,
            }
        )

    return objects


def load_deep_sky_objects(
    dso_paths: list[Path],
    constellation_name_map: dict[str, str],
    localized_names: dict[str, str],
    supplemental_dso_path: Path | None = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for dso_path in dso_paths:
        if not dso_path.exists():
            continue

        with dso_path.open(encoding="utf-8", errors="ignore") as handle:
            header = handle.readline()

        if header.startswith("Object,Type,RA (h),DEC (deg)"):
            loaded_objects = load_stardroid_dso_objects(dso_path, constellation_name_map, localized_names)
        else:
            loaded_objects = load_openngc_objects(dso_path, constellation_name_map, localized_names)

        for item in loaded_objects:
            key = build_dso_key(item["name"], item.get("messier"), item.get("common_names", []), item.get("catalog_id"))
            if not key:
                continue
            existing = merged.get(key)
            if existing is None:
                merged[key] = dict(item)
                continue
            merge_dso_entry(existing, item)

    for item in load_supplemental_deep_sky_objects(supplemental_dso_path, constellation_name_map, localized_names):
        key = build_dso_key(item["name"], item.get("messier"), item.get("common_names", []), item.get("catalog_id"))
        existing = merged.get(key)
        if existing is None:
            merged[key] = item
        else:
            merge_dso_entry(existing, item)

    return list(merged.values())
