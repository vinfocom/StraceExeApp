import os
from typing import Any, Iterable

import pandas as pd
import requests


def backend_db_mode_enabled() -> bool:
    return os.getenv("DB_ACCESS_MODE", "direct").strip().lower() == "backend"


def _base_url() -> str:
    return (
        os.getenv("SIGNAL_TRACKERS_API_URL", "https://s-traccceer.vinfocom.co.in")
        .strip()
        .rstrip("/")
    )


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    bridge_key = os.getenv("SIGNAL_TRACKERS_BRIDGE_KEY", "").strip()
    if bridge_key:
        headers["X-Python-Bridge-Key"] = bridge_key
    return headers


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    url = f"{_base_url()}/{path.lstrip('/')}"
    response = requests.request(
        method=method,
        url=url,
        params=params,
        json=json_body,
        headers=_headers(),
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Signal-Trackers API error {response.status_code} for {url}: {response.text[:600]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Signal-Trackers API returned non-JSON response for {url}: {response.text[:600]}"
        ) from exc
    if isinstance(payload, dict) and payload.get("Status") == 0:
        raise RuntimeError(f"Signal-Trackers API failure for {url}: {payload}")
    return payload if isinstance(payload, dict) else {"Data": payload}


def _payload_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("Data")
    if rows is None:
        rows = payload.get("data")
    if rows is None:
        rows = payload.get("Rows")
    if rows is None:
        rows = payload.get("rows")
    if isinstance(rows, list):
        return rows
    if isinstance(payload, list):
        return payload
    return []


def _iter_chunks(rows: list[dict[str, Any]], size: int):
    for idx in range(0, len(rows), size):
        yield rows[idx:idx + size]


def fetch_site_noml(project_id: int) -> pd.DataFrame:
    page_size = 20000
    offset = 0
    out: list[dict[str, Any]] = []

    while True:
        payload = _request(
            "GET",
            "/api/MapView/GetSiteNoMl",
            params={
                "projectId": project_id,
                "limit": page_size,
                "offset": offset,
            },
            timeout=120,
        )
        rows = _payload_rows(payload)
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size

    return pd.DataFrame(out)


def fetch_site_prediction(project_id: int) -> pd.DataFrame:
    page_size = 2000
    offset = 0
    out: list[dict[str, Any]] = []

    while True:
        payload = _request(
            "GET",
            "/api/MapView/GetSitePrediction",
            params={
                "projectId": project_id,
                "version": "combined",
                "limit": page_size,
                "offset": offset,
            },
            timeout=120,
        )
        rows = _payload_rows(payload)
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size

    return pd.DataFrame(out)


def fetch_saved_polygons(project_id: int) -> list[dict[str, Any]]:
    page_size = 50000
    offset = 0
    out: list[dict[str, Any]] = []

    while True:
        payload = _request(
            "GET",
            "/api/MapView/ListSavedPolygons",
            params={
                "projectId": project_id,
                "limit": page_size,
                "offset": offset,
            },
            timeout=120,
        )
        rows = _payload_rows(payload)
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size

    return out


def fetch_drive_test_rows(session_ids: Iterable[int], include_neighbour: bool = True) -> pd.DataFrame:
    session_ids = [int(s) for s in session_ids]
    page_size = 50000
    offset = 0
    out: list[dict[str, Any]] = []

    while True:
        payload = _request(
            "POST",
            "/api/PythonBridge/GetDriveTestRows",
            json_body={
                "SessionIds": session_ids,
                "IncludeNeighbour": include_neighbour,
                "Limit": page_size,
                "Offset": offset,
            },
            timeout=180,
        )
        rows = _payload_rows(payload)
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size

    return pd.DataFrame(out)


def save_prediction_data(project_id: int, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    rows = df.where(pd.notnull(df), None).to_dict(orient="records")
    inserted = 0
    for chunk in _iter_chunks(rows, 5000):
        payload = _request(
            "POST",
            "/api/PythonBridge/SavePredictionData",
            json_body={"ProjectId": int(project_id), "Rows": chunk},
            timeout=180,
        )
        inserted += int(payload.get("Inserted", len(chunk)))
    return inserted


def save_lte_prediction_results(project_id: int, job_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    rows = df.where(pd.notnull(df), None).to_dict(orient="records")
    inserted = 0
    for chunk in _iter_chunks(rows, 5000):
        payload = _request(
            "POST",
            "/api/PythonBridge/SaveLtePredictionResults",
            json_body={
                "ProjectId": int(project_id),
                "JobId": str(job_id),
                "Rows": chunk,
            },
            timeout=180,
        )
        inserted += int(payload.get("Inserted", len(chunk)))
    return inserted


def save_lte_prediction_refined(project_id: int, job_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    rows = df.where(pd.notnull(df), None).to_dict(orient="records")
    inserted = 0
    for chunk in _iter_chunks(rows, 5000):
        payload = _request(
            "POST",
            "/api/PythonBridge/SaveLtePredictionRefined",
            json_body={
                "ProjectId": int(project_id),
                "JobId": str(job_id),
                "Rows": chunk,
            },
            timeout=180,
        )
        inserted += int(payload.get("Inserted", len(chunk)))
    return inserted


def prediction_debug_summary(project_id: int) -> dict[str, Any]:
    return _request(
        "GET",
        "/api/PythonBridge/PredictionDebugSummary",
        params={"projectId": int(project_id)},
        timeout=60,
    )


def fetch_available_polygons(project_id: int | None = None, company_id: int | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if project_id is not None:
        params["projectId"] = int(project_id)
    if company_id is not None:
        params["company_id"] = int(company_id)

    payload = _request(
        "GET",
        "/api/MapView/GetAvailablePolygons",
        params=params or None,
        timeout=120,
    )
    return _payload_rows(payload)


def fetch_projects(company_id: int | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if company_id is not None:
        params["company_id"] = int(company_id)

    payload = _request(
        "GET",
        "/api/MapView/GetProjects",
        params=params or None,
        timeout=120,
    )
    return _payload_rows(payload)
