from __future__ import annotations

import json
from typing import Iterable, List, Set

from sqlalchemy import text

from extensions import db

FEATURE_REPORT_GENERATION = "report_generation"
FEATURE_BENCHMARK_TAB = "benchmark_tab"
FEATURE_RUN_PREDICTION = "run_prediction"
FEATURE_GRID_FETCH = "grid_fetch"

DEFAULT_FEATURES: Set[str] = {
    FEATURE_REPORT_GENERATION,
    FEATURE_BENCHMARK_TAB,
    FEATURE_RUN_PREDICTION,
    FEATURE_GRID_FETCH,
}

ALIASES = {
    "report": FEATURE_REPORT_GENERATION,
    "report_generation": FEATURE_REPORT_GENERATION,
    "reportgeneration": FEATURE_REPORT_GENERATION,
    "generate_report": FEATURE_REPORT_GENERATION,
    "generate_pdf": FEATURE_REPORT_GENERATION,
    "pdf_report": FEATURE_REPORT_GENERATION,
    "benchmark": FEATURE_BENCHMARK_TAB,
    "benchmark_tab": FEATURE_BENCHMARK_TAB,
    "operatorcomparison": FEATURE_BENCHMARK_TAB,
    "operator_comparison": FEATURE_BENCHMARK_TAB,
    "run_prediction": FEATURE_RUN_PREDICTION,
    "runprediction": FEATURE_RUN_PREDICTION,
    "prediction": FEATURE_RUN_PREDICTION,
    "lte_prediction": FEATURE_RUN_PREDICTION,
    "run_lte_prediction": FEATURE_RUN_PREDICTION,
    "grid_fetch": FEATURE_GRID_FETCH,
    "gridfetch": FEATURE_GRID_FETCH,
    "fetch_grid": FEATURE_GRID_FETCH,
    "fetchgrid": FEATURE_GRID_FETCH,
    "grid_api": FEATURE_GRID_FETCH,
    "grid_compute": FEATURE_GRID_FETCH,
    "compute_grid": FEATURE_GRID_FETCH,
}


def _canonicalize(value: str | None) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return ALIASES.get(key, key)


def _split_raw(raw: str | None) -> List[str]:
    text_value = str(raw or "").strip()
    if not text_value:
        return []

    if (text_value.startswith("[") and text_value.endswith("]")) or (
        text_value.startswith("{") and text_value.endswith("}")
    ):
        try:
            parsed = json.loads(text_value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except Exception:
            pass

    parts: List[str] = []
    for sep in [",", ";", "|", "\n"]:
        if sep in text_value:
            parts = [x.strip() for x in text_value.replace("\n", ",").replace(";", ",").replace("|", ",").split(",")]
            break
    if not parts:
        parts = [text_value]
    return [p for p in parts if p]


def normalize_features(values: Iterable[str]) -> Set[str]:
    items: Set[str] = set()
    for value in values:
        for token in _split_raw(value):
            key = _canonicalize(token)
            if key in DEFAULT_FEATURES:
                items.add(key)
    return items


def get_enabled_features_for_user(user_id: int) -> Set[str]:
    if not user_id:
        return set()

    try:
        sql = text(
            """
            SELECT lfa.license_id, lfa.feature_codes
            FROM tbl_company_user_license_issued lic
            LEFT JOIN license_feature_access lfa ON lfa.license_id = lic.id
            WHERE lic.tbl_user_id = :uid
              AND lic.status = 1
              AND DATE(lic.valid_till) >= UTC_DATE()
            ORDER BY lic.valid_till DESC, lic.created_on DESC, lic.id DESC
            LIMIT 1
            """
        )
        row = db.session.execute(sql, {"uid": int(user_id)}).first()
        if not row:
            return set()

        has_feature_config = row[0] is not None
        if not has_feature_config:
            return set()

        raw_value = "" if row[1] is None else str(row[1])
        return normalize_features([raw_value])
    except Exception:
        return set()


def has_feature_access(user_id: int, feature_key: str, default_allow: bool = False) -> bool:
    # Backend feature gating is intentionally disabled.
    # Feature visibility/access is controlled from the frontend.
    return True
