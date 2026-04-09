import pandas as pd
import subprocess
import os
from .Sector_wise_prediction_code_copy import run_prediction_from_api
from .lte_ml_correction_final import run_ml_from_api

import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

# 🔥 Load .env
load_dotenv()

# 🔥 Get DB URL
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL not found in .env")

# 🔥 Create engine
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True
)

# 🔹 TEMP DB MOCK (replace later)
def fetch_site_data(project_id):

    query = f"""
    SELECT *
    FROM site_prediction
    WHERE tbl_project_id = {project_id}
    """

    df = pd.read_sql(query, engine)

    if df.empty:
        raise ValueError("No site data found")

    operator = df["cluster"].dropna().iloc[0]

    return df, operator


def fetch_drive_data(session_ids, operator):

    import os

    session_str = ",".join(map(str, session_ids))
    key = f"{operator}_{session_str}"
    path = f"cache/drive_{key}.parquet"

    if os.path.exists(path):
        print("⚡ CACHE HIT")
        return pd.read_parquet(path)

    query = f"""
    SELECT lat, lon, rsrp, rsrq, sinr
    FROM tbl_network_log
    WHERE session_id IN ({session_str})
    AND LOWER(COALESCE(m_alpha_long, m_alpha_short)) = LOWER('{operator}')

    UNION ALL

    SELECT lat, lon, rsrp, rsrq, sinr
    FROM tbl_network_log_neighbour
    WHERE session_id IN ({session_str})
    AND LOWER(COALESCE(m_alpha_long, m_alpha_short)) = LOWER('{operator}')
    """

    df = pd.read_sql(query, engine)

    df.to_parquet(path)

    return df


def fetch_building_data(project_id):

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
    building_df.to_csv(building_path, index=False)

    run_prediction_from_api({
        "site": site_path,
        "drive": drive_path,
        "building": building_path,
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
