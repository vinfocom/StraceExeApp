import os
from typing import Any, Iterable

import pandas as pd
import requests


def backend_db_mode_enabled() -> bool:
    return os.getenv("DB_ACCESS_MODE", "backend").strip().lower() == "backend"


def _base_url() -> str:
    raw = os.getenv("SIGNAL_TRACKERS_API_URL", "http://localhost:5224").strip()
    # Guard against a known production typo that causes DNS/connect timeouts.
    raw = raw.replace("s-traccceer.vinfocom.co.in", "stracer.vinfocom.co.in")
    return raw.rstrip("/")


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
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "html" in content_type:
        raise RuntimeError(
            f"Signal-Trackers API returned HTML for {url} (Content-Type: {content_type}). "
            "Check SIGNAL_TRACKERS_API_URL and ensure it points to the C# API host."
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


def fetch_site_prediction(project_id: int, version: str = "combined") -> pd.DataFrame:
    page_size = 2000
    offset = 0
    out: list[dict[str, Any]] = []

    while True:
        payload = _request(
            "GET",
            "/api/MapView/GetSitePrediction",
            params={
                "projectId": project_id,
                "version": (version or "combined"),
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


def fetch_lte_tilt_baseline_results(project_id: int, operator: str | None = None) -> pd.DataFrame:
    page_size = 5000
    offset = 0
    out: list[dict[str, Any]] = []

    params: dict[str, Any] = {"projectId": int(project_id)}
    normalized_operator = str(operator or "").strip()
    if normalized_operator and normalized_operator.lower() != "all":
        params["operator"] = normalized_operator

    while True:
        payload = _request(
            "GET",
            "/api/PythonBridge/GetLteTiltBaselineResults",
            params={
                **params,
                "limit": page_size,
                "offset": offset,
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


def fetch_site_prediction_base(
    *,
    project_id: int,
    cell_id: str | None = None,
    node_b_id: str | None = None,
    sector: str | None = None,
) -> pd.DataFrame:
    params: dict[str, Any] = {"project_id": int(project_id)}
    if cell_id:
        params["cell_id"] = str(cell_id).strip()
    if node_b_id:
        params["node_b_id"] = str(node_b_id).strip()
    if sector:
        params["sector"] = str(sector).strip()

    payload = _request(
        "GET",
        "/api/MapView/GetSitePredictionBase",
        params=params,
        timeout=120,
    )
    return pd.DataFrame(_payload_rows(payload))


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


def save_lte_prediction_optimised_results(project_id: int, job_id: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    rows = df.where(pd.notnull(df), None).to_dict(orient="records")
    inserted = 0
    for chunk in _iter_chunks(rows, 5000):
        payload = _request(
            "POST",
            "/api/PythonBridge/SaveLtePredictionOptimisedResults",
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


def fetch_project_by_id(project_id: int) -> dict[str, Any] | None:
    payload = _request(
        "GET",
        "/api/PythonBridge/GetProject",
        params={"projectId": int(project_id)},
        timeout=120,
    )
    project = payload.get("Data")
    return project if isinstance(project, dict) else None


def fetch_project_regions(project_id: int) -> list[dict[str, Any]]:
    payload = _request(
        "GET",
        "/api/PythonBridge/GetProjectRegions",
        params={"projectId": int(project_id)},
        timeout=120,
    )
    return _payload_rows(payload)


def fetch_report_network_logs(session_ids: Iterable[int]) -> pd.DataFrame:
    session_ids = [int(s) for s in session_ids]
    page_size = 50000
    offset = 0
    out: list[dict[str, Any]] = []

    while True:
        payload = _request(
            "POST",
            "/api/PythonBridge/GetReportNetworkLogs",
            json_body={
                "SessionIds": session_ids,
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


def fetch_sessions(session_ids: Iterable[int]) -> pd.DataFrame:
    session_ids = [int(s) for s in session_ids]
    payload = _request(
        "POST",
        "/api/PythonBridge/GetSessions",
        json_body={"SessionIds": session_ids},
        timeout=120,
    )
    return pd.DataFrame(_payload_rows(payload))


def fetch_user_by_id(user_id: int) -> dict[str, Any] | None:
    payload = _request(
        "GET",
        "/api/PythonBridge/GetUser",
        params={"userId": int(user_id)},
        timeout=120,
    )
    user = payload.get("Data")
    return user if isinstance(user, dict) else None


def fetch_user_thresholds(user_id: int) -> dict[str, Any] | None:
    payload = _request(
        "GET",
        "/api/PythonBridge/GetUserThresholds",
        params={"userId": int(user_id)},
        timeout=120,
    )
    thresholds = payload.get("Data")
    return thresholds if isinstance(thresholds, dict) else None


def update_project_download_path(project_id: int, download_path: str) -> bool:
    payload = _request(
        "POST",
        "/api/PythonBridge/UpdateProjectDownloadPath",
        json_body={
            "ProjectId": int(project_id),
            "DownloadPath": str(download_path),
        },
        timeout=120,
    )
    return bool(payload.get("Updated"))
