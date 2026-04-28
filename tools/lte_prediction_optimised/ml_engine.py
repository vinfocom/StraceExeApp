import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv
import os
from utils.signaltrackers_client import (
    backend_db_mode_enabled,
    fetch_site_prediction,
)

# 🔥 Import your main RF functions
from .Sector_wise_prediction_code_copy import (
    calibrate_site,
    compute_predictions_parallel,
    generate_grid
)
from .Sector_wise_prediction_code_copy import run_prediction_from_api

# ==========================================================
# DB CONNECTION
# ==========================================================

load_dotenv()
USE_BACKEND_PROXY = backend_db_mode_enabled()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
engine = None
if not USE_BACKEND_PROXY:
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL not found while DB_ACCESS_MODE=direct")
    engine = create_engine(DATABASE_URL)


def _normalized_col(name):
    return str(name).strip().lower().replace(" ", "_")


def _pick_col(df, *candidates):
    normalized_to_actual = {_normalized_col(c): c for c in df.columns}
    for candidate in candidates:
        hit = normalized_to_actual.get(_normalized_col(candidate))
        if hit:
            return hit
    return None


# ==========================================================
# FETCH BASELINE (AS DRIVE TEST)
# ==========================================================

def fetch_baseline(project_id):
    if USE_BACKEND_PROXY or engine is None:
        # In backend-bridge mode this table is not directly reachable from Python.
        # Continue with default COST231 by returning an empty baseline.
        return pd.DataFrame(columns=["lat", "lon", "rsrp", "cell_id", "Node_Cell_ID"])

    query = f"""
    SELECT lat, lon, pred_rsrp as rsrp, cell_id, node_b_cell_id, nodeb_id_cell_id
    FROM lte_prediction_baseline_results
    WHERE project_id = {int(project_id)}
    """

    df = pd.read_sql(query, engine)
    if df.empty:
        return pd.DataFrame(columns=["lat", "lon", "rsrp", "cell_id", "Node_Cell_ID"])

    if "cell_id" not in df.columns:
        for candidate in ("node_b_cell_id", "nodeb_id_cell_id"):
            if candidate in df.columns:
                df["cell_id"] = df[candidate]
                break

    df["Node_Cell_ID"] = df["cell_id"].astype(str).str.strip()
    return df


# ==========================================================
# FETCH ORIGINAL SITE DATA
# ==========================================================

def fetch_site_data(project_id):
    if USE_BACKEND_PROXY:
        df = fetch_site_prediction(int(project_id), version="original")
    else:
        query = f"""
        SELECT *
        FROM site_prediction
        WHERE tbl_project_id = {int(project_id)}
        """
        df = pd.read_sql(query, engine)

    if df.empty:
        raise ValueError("No site_prediction rows found for project")

    # 🔥 MATCH YOUR SCRIPT FORMAT
    df = df.rename(columns={
        "latitude": "lat",
        "longitude": "lon",
        "e_tilt": "Etilt",
        "m_tilt": "Mtilt",
        "height": "Height"
    })

    if "cell_id" not in df.columns:
        combined_col = _pick_col(df, "node_b_cell_id", "nodeb_id_cell_id")
        if combined_col:
            df["cell_id"] = df[combined_col]
        elif "site_id" in df.columns:
            df["cell_id"] = df["site_id"]
        else:
            raise ValueError("site_prediction is missing cell_id")

    df["Node_Cell_ID"] = df["cell_id"].astype(str).str.strip()

    for col in ("Etilt", "Mtilt", "Height", "tx_power", "azimuth"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # default frequency
    if "frequency_mhz" not in df.columns:
        df["frequency_mhz"] = 1800
    df["frequency_mhz"] = pd.to_numeric(df["frequency_mhz"], errors="coerce").fillna(1800.0)

    return df


# ==========================================================
# FETCH OPTIMIZED SITE DATA
# ==========================================================

# ==========================================================
# FETCH OPTIMIZED SITE DATA (OPERATOR-WISE)
# ==========================================================

def fetch_optimized_sites(project_id, operator):
    if USE_BACKEND_PROXY:
        df = fetch_site_prediction(int(project_id), version="updated")
    else:
        # 🔥 Much simpler query! No JOIN needed since the operator is right here.
        query = f"""
        SELECT *
        FROM site_prediction_optimized
        WHERE tbl_project_id = {int(project_id)}
        """
        df = pd.read_sql(query, engine)

    if df.empty:
        raise ValueError("No optimized site rows found for project")

    operator_col = _pick_col(
        df,
        "cluster_name",
        "cluster",
        "operator",
        "optimized_cluster",
        "provider",
        "m_alpha_long",
        "m_alpha_short",
    )
    normalized_operator = str(operator or "").strip().lower()
    if operator_col and normalized_operator:
        filtered = df[df[operator_col].astype(str).str.strip().str.lower() == normalized_operator]
        if not filtered.empty:
            df = filtered

    df = df.rename(columns={
        "latitude": "lat",
        "longitude": "lon",

        # 🔥 CRITICAL FIX
        "e_tilt": "electrical_tilt",
        "m_tilt": "mechanical_tilt",
        "height": "antenna_height"
    })
    
    required_with_defaults = {
        "lat": 0.0,
        "lon": 0.0,
        "azimuth": 0.0,
        "tx_power": 46.0,
        "electrical_tilt": 0.0,
        "mechanical_tilt": 0.0,
        "antenna_height": 30.0,
    }

    for col, default_value in required_with_defaults.items():
        if col not in df.columns:
            df[col] = default_value
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(default_value)

    combined_col = _pick_col(df, "node_b_cell_id", "nodeb_id_cell_id")
    if "cell_id" not in df.columns:
        if combined_col:
            df["cell_id"] = df[combined_col]
        elif "site_id" in df.columns:
            df["cell_id"] = df["site_id"]
        else:
            df["cell_id"] = [f"cell_{i}" for i in range(len(df))]

    # ✅ CLEAN STRING (VERY IMPORTANT)
    df["cell_id"] = df["cell_id"].astype(str).str.strip()
    if "node_b_id" in df.columns:
        df["node_b_id"] = df["node_b_id"].astype(str).str.strip()

    # ✅ Use combined id when available, otherwise fallback
    if combined_col:
        df["Node_Cell_ID"] = df[combined_col].astype(str).str.strip()
    elif "node_b_id" in df.columns:
        df["Node_Cell_ID"] = (
            df["node_b_id"].astype(str).str.strip()
            + "_"
            + df["cell_id"].astype(str).str.strip()
        )
    else:
        df["Node_Cell_ID"] = df["cell_id"].astype(str).str.strip()

    print(f"✅ Filtered for Operator: {operator}")
    print("✅ Total rows:", len(df))
    print("✅ Total cells:", df["Node_Cell_ID"].nunique())
    print("Sample IDs:", df["Node_Cell_ID"].unique()[:5])

    # default frequency
    if "frequency_mhz" not in df.columns:
        df["frequency_mhz"] = 1800

    return df


# ==========================================================
# K1 K2 CALCULATION
# ==========================================================

def compute_k1k2(baseline_df, site_df):

    k1k2_map = {}

    for cid in site_df["Node_Cell_ID"].unique():

        site_rows = site_df[site_df["Node_Cell_ID"] == cid]
        dt_rows   = baseline_df[baseline_df["Node_Cell_ID"] == cid]

        if len(dt_rows) < 10:
            continue

        freq = site_rows["frequency_mhz"].iloc[0]

        k1, k2 = calibrate_site(
            dt_rows,
            site_rows,
            site_rows["tx_power"].iloc[0],
            18, 2, freq
        )

        k1k2_map[cid] = (k1, k2)

    return k1k2_map


# ==========================================================
# OPTIMIZED PREDICTION ONLY
# ==========================================================

def run_prediction_only_optimized(opt_sites, k1k2_map, params):

    final_list = []

    # 🔥 ALL sites for interference
    opt_site_records = opt_sites.to_dict("records")

    unique_cells = opt_sites["Node_Cell_ID"].unique()

    print(f"🚀 Total cells to process: {len(unique_cells)}")

    for cid in unique_cells:

        print(f"\n⚡ Running optimized cell: {cid}")

        site_rows = opt_sites[opt_sites["Node_Cell_ID"] == cid]

        k1, k2 = k1k2_map.get(cid, (0, 0))

        if k1 != 0:
            print(f"   ✔ Using K1={k1:.2f}, K2={k2:.2f}")
        else:
            print(f"   ⚠ Using COST231")

        cell_params = params.copy()

        cell_params.update({
            "k1": k1,
            "k2": k2,
            "all_sites_rows": opt_site_records
        })

        pts = generate_grid(
            site_rows,
            cell_params["radius"],
            cell_params["grid_resolution"]
        )

        print(f"   📍 Grid points: {len(pts)}")

        # ⏱ START TIMER
        import time
        start = time.time()

        rsrp, rsrq, sinr = compute_predictions_parallel(
            pts,
            site_rows,
            cell_params,
            n_workers=cell_params.get("n_workers")
        )

        print(f"   ⏱ Time taken: {round(time.time() - start, 2)} sec")

        pts["pred_rsrp"] = rsrp
        pts["pred_rsrq"] = rsrq
        pts["pred_sinr"] = sinr
        pts["Node_Cell_ID"] = cid

        final_list.append(pts)

        print(f"✅ Completed cell: {cid}")   # 🔥 KEY LINE

    return pd.concat(final_list, ignore_index=True)


# ==========================================================
# REPLACE BASELINE CELLS
# ==========================================================

def replace_cells(baseline_df, optimized_df):

    replace_ids = optimized_df["Node_Cell_ID"].unique()

    baseline_df = baseline_df[
        ~baseline_df["Node_Cell_ID"].isin(replace_ids)
    ]

    final_df = pd.concat([baseline_df, optimized_df], ignore_index=True)

    return final_df
