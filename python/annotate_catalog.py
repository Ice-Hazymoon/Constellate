#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from astroquery.vizier import Vizier
from skyfield.data import hipparcos

from annotate_options import batched


def normalize_catalog_frame(frame: pd.DataFrame) -> pd.DataFrame:
    catalog = frame.copy()
    if catalog.empty:
        return pd.DataFrame(columns=["magnitude", "ra_degrees", "dec_degrees"]).rename_axis("hip")
    if "hip" not in catalog.columns:
        catalog = catalog.reset_index()
    catalog = catalog.rename(columns={"HIP": "hip", "RAICRS": "ra_degrees", "DEICRS": "dec_degrees", "Vmag": "magnitude"})
    catalog["hip"] = catalog["hip"].astype(int)
    for column in ("magnitude", "ra_degrees", "dec_degrees"):
        catalog[column] = pd.to_numeric(catalog[column], errors="coerce")
    catalog = catalog.replace([np.inf, -np.inf], np.nan).dropna(subset=["ra_degrees", "dec_degrees", "magnitude"])
    catalog = catalog.drop_duplicates(subset="hip").set_index("hip").sort_index()
    return catalog


def fetch_minimal_catalog(
    catalog_path: Path,
    required_hips: set[int],
    existing_catalog: pd.DataFrame | None = None,
    prefer_full_catalog: bool = True,
) -> pd.DataFrame:
    Vizier.ROW_LIMIT = -1
    rows: list[dict[str, float | int]] = []

    if prefer_full_catalog:
        try:
            table = Vizier(columns=["HIP", "RAICRS", "DEICRS", "Vmag"]).get_catalogs("I/239/hip_main")[0]
            frame = table.to_pandas()[["HIP", "RAICRS", "DEICRS", "Vmag"]]
            frame["HIP"] = frame["HIP"].astype(int)
            frame = frame[frame["HIP"].isin(sorted(required_hips))]
            rows.extend(frame.to_dict("records"))
        except Exception:
            pass

    if not rows:
        for chunk in batched(sorted(required_hips), 20):
            result = Vizier(columns=["HIP", "RAICRS", "DEICRS", "Vmag"]).query_constraints(
                catalog="I/239/hip_main",
                HIP=",".join(str(hip) for hip in chunk),
            )
            if not result:
                continue
            for entry in result[0]:
                ra_value = entry["RAICRS"]
                dec_value = entry["DEICRS"]
                magnitude_value = entry["Vmag"]
                if np.ma.is_masked(ra_value) or np.ma.is_masked(dec_value) or np.ma.is_masked(magnitude_value):
                    continue
                rows.append(
                    {
                        "hip": int(entry["HIP"]),
                        "magnitude": float(magnitude_value),
                        "ra_degrees": float(ra_value),
                        "dec_degrees": float(dec_value),
                    }
                )

    fetched_catalog = normalize_catalog_frame(pd.DataFrame(rows))
    if existing_catalog is not None and not existing_catalog.empty:
        fetched_catalog = normalize_catalog_frame(pd.concat([existing_catalog.reset_index(), fetched_catalog.reset_index()], ignore_index=True))

    if fetched_catalog.empty:
        raise RuntimeError("failed to build minimal Hipparcos cache from VizieR")
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    fetched_catalog.to_csv(catalog_path)
    return fetched_catalog


def load_catalog(catalog_path: Path, required_hips: set[int]) -> pd.DataFrame:
    if catalog_path.exists():
        if catalog_path.suffix.lower() in {".dat", ".gz"}:
            with catalog_path.open("rb") as handle:
                catalog = hipparcos.load_dataframe(handle)
            return catalog[catalog["magnitude"].notna()].copy()

        catalog = normalize_catalog_frame(pd.read_csv(catalog_path))
        missing = required_hips - set(int(value) for value in catalog.index)
        if not missing:
            return catalog
        return fetch_minimal_catalog(catalog_path, missing, existing_catalog=catalog, prefer_full_catalog=False)

    return fetch_minimal_catalog(catalog_path, required_hips, prefer_full_catalog=False)
