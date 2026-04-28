import pandas as pd
import subprocess
import os
from .Sector_wise_prediction_code_copy import run_prediction_from_api
from .lte_ml_correction_final import run_ml_from_api

import os
from sqlalchemy import create_engine
from dotenv import load_dotenv
from utils.signaltrackers_client import (
    backend_db_mode_enabled,
    fetch_drive_test_rows,
    fetch_saved_polygons,
    fetch_site_prediction,
)

# 🔥 Load .env
load_dotenv()

USE_BACKEND_PROXY = backend_db_mode_enabled()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
engine = None
if not USE_BACKEND_PROXY:
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL not found while DB_ACCESS_MODE=direct")
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True
    )


def _normalized_col(name):
    return str(name).strip().lower().replace(" ", "_")


def _pick_col(df, *candidates):
    normalized_to_actual = {_normalized_col(c): c for c in df.columns}
    for candidate in candidates:
        hit = normalized_to_actual.get(_normalized_col(candidate))
        if hit:
            return hit
    return None


def _ensure_tx_power(df):
    tx_col = _pick_col(
        df,
        "tx_power",
        "txpower",
        "maximum_transmission_power_of_resource",
        "maximumtransmissionpowerofresource",
        "maximum_transmit_power_of_resource",
        "maximumtransmitpowerofresource",
    )
    if tx_col and tx_col != "tx_power":
        df = df.rename(columns={tx_col: "tx_power"})
    if "tx_power" not in df.columns:
        df["tx_power"] = 46.0
    df["tx_power"] = pd.to_numeric(df["tx_power"], errors="coerce").fillna(46.0)
    return df

# 🔹 TEMP DB MOCK (replace later)
def fetch_site_data(project_id):
    if USE_BACKEND_PROXY:
        # Baseline prediction should use the originally uploaded site rows.
        # "combined" can depend on optimized merge state and may hide valid uploads.
        df = fetch_site_prediction(int(project_id), version="original")
    else:
        query = f"""
        SELECT *
        FROM site_prediction
        WHERE tbl_project_id = {project_id}
        """
        df = pd.read_sql(query, engine)

    if df.empty:
        raise ValueError(f"No site_prediction rows found for project_id={project_id}")

    df.columns = [str(c).strip() for c in df.columns]
    rename_map = {}
    if "latitude" in df.columns and "lat" not in df.columns:
        rename_map["latitude"] = "lat"
    if "longitude" in df.columns and "lon" not in df.columns:
        rename_map["longitude"] = "lon"
    if rename_map:
        df = df.rename(columns=rename_map)

    if "lat" not in df.columns or "lon" not in df.columns:
        raise ValueError("site_prediction is missing latitude/longitude columns")

    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["lat", "lon"]).copy()
    if df.empty:
        raise ValueError(f"site_prediction rows exist for project_id={project_id}, but all rows have invalid latitude/longitude")

    df = _ensure_tx_power(df)

    operator_col = None
    for col in ["cluster", "provider", "operator", "project_provider", "m_alpha_long", "m_alpha_short"]:
        if col in df.columns and df[col].astype(str).str.strip().ne("").any():
            operator_col = col
            break

    if operator_col is None:
        # Keep the prediction runnable even if operator text is absent in uploads.
        operator = "all"
    else:
        operator_series = df[operator_col].dropna().astype(str).str.strip()
        operator_series = operator_series[operator_series.ne("")]
        operator = operator_series.iloc[0] if not operator_series.empty else "all"

    return df, operator


def fetch_drive_data(session_ids, operator):

    import os

    session_str = ",".join(map(str, session_ids))
    key = f"{operator}_{session_str}"
    path = f"cache/drive_{key}.parquet"

    if os.path.exists(path):
        print("⚡ CACHE HIT")
        return pd.read_parquet(path)

    if USE_BACKEND_PROXY:
        df = fetch_drive_test_rows([int(s) for s in session_ids], include_neighbour=True)
        if not df.empty and str(operator).strip().lower() not in ("", "all"):
            provider_like_cols = [c for c in ["m_alpha_long", "m_alpha_short", "provider", "operator"] if c in df.columns]
            if provider_like_cols:
                mask = pd.Series(False, index=df.index)
                normalized_operator = str(operator).strip().lower()
                for col in provider_like_cols:
                    mask = mask | (df[col].astype(str).str.strip().str.lower() == normalized_operator)
                df = df[mask]
        expected_cols = ["lat", "lon", "rsrp", "rsrq", "sinr"]
        for col in expected_cols:
            if col not in df.columns:
                df[col] = None
        df = df[expected_cols]
    else:
        query = f"""
        SELECT lat, lon, rsrp, rsrq, sinr
        FROM tbl_network_log
        WHERE session_id IN ({session_str})
        {"AND LOWER(COALESCE(m_alpha_long, m_alpha_short)) = LOWER('" + str(operator) + "')" if str(operator).strip().lower() not in ("", "all") else ""}

        UNION ALL

        SELECT lat, lon, rsrp, rsrq, sinr
        FROM tbl_network_log_neighbour
        WHERE session_id IN ({session_str})
        {"AND LOWER(COALESCE(m_alpha_long, m_alpha_short)) = LOWER('" + str(operator) + "')" if str(operator).strip().lower() not in ("", "all") else ""}
        """

        df = pd.read_sql(query, engine)

    df.to_parquet(path)

    return df


def fetch_building_data(project_id):
    if USE_BACKEND_PROXY:
        polygons = fetch_saved_polygons(int(project_id))
        df = pd.DataFrame(polygons)
        if df.empty:
            return df

        # Normalize geometry column for downstream loader compatibility.
        if "region" not in df.columns:
            for candidate in ("wkt", "polygon", "geometry"):
                if candidate in df.columns:
                    df["region"] = df[candidate]
                    break
        return df
    query = f"""
    SELECT *
    FROM tbl_savepolygon
    WHERE project_id = {project_id}
    """

    return pd.read_sql(query, engine)


def fetch_polygon_data(project_id):
    return {
        "type": "Polygon",
        "coordinates": [[[77.1, 28.6], [77.2, 28.6], [77.2, 28.7], [77.1, 28.7], [77.1, 28.6]]]
    }


# 🚀 RF ENGINE
def run_rf_prediction_fast(site_df, drive_df, building_df, params):

    temp_dir = "temp_rf"
    os.makedirs(temp_dir, exist_ok=True)

    site_path = f"{temp_dir}/site.csv"
    drive_path = f"{temp_dir}/drive.csv"
    building_path = f"{temp_dir}/building.csv"

    site_df.to_csv(site_path, index=False)
    drive_df.to_csv(drive_path, index=False)

    building_arg = None
    if building_df is not None and not building_df.empty:
        building_df.to_csv(building_path, index=False)
        building_arg = building_path

    run_prediction_from_api({
        "site": site_path,
        "drive": drive_path,
        "building": building_arg,
        "polygon_area": params.get("polygon_area"),
        "radius": params["radius"],
        "grid_resolution": params["grid"],
        "outdir": temp_dir,
        "n_workers": params["workers"],
        "calibrate": True
    })

    return pd.read_csv(f"{temp_dir}/prediction_ALL_SITES.csv")


def run_ml_fast(pred_df, drive_df):
    return run_ml_from_api(pred_df, drive_df)


# 🔹 GRID
def grid_drive_test(input_file, output_file):
    df = pd.read_csv(input_file)
    df.to_csv(output_file, index=False)
