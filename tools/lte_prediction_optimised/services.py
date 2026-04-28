import datetime
import os
import threading
import traceback
import uuid

import pandas as pd
from sqlalchemy import create_engine

from utils.signaltrackers_client import (
    backend_db_mode_enabled,
    save_lte_prediction_optimised_results,
)

from .ml_engine import (
    compute_k1k2,
    fetch_baseline,
    fetch_optimized_sites,
    fetch_site_data,
    run_prediction_only_optimized,
)

JOBS = {}
USE_BACKEND_PROXY = backend_db_mode_enabled()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
engine = None
if not USE_BACKEND_PROXY and DATABASE_URL:
    engine = create_engine(DATABASE_URL)


class LTEPredictionService_optimised:
    def submit(self, cfg):
        job_id = str(uuid.uuid4())
        JOBS[job_id] = {"status": "queued"}
        threading.Thread(target=self._run, args=(job_id, cfg), daemon=True).start()
        return {"job_id": job_id}

    def get(self, job_id):
        return JOBS.get(job_id)

    def _run(self, job_id, cfg):
        try:
            project_id = int(cfg["project_id"])
            requested_operator = self._normalize_operator(cfg.get("operator"))

            self._update(job_id, "running", "Loading baseline")
            baseline_df = fetch_baseline(project_id)
            if baseline_df.empty:
                self._update(job_id, "running", "Baseline unavailable, using default propagation")

            self._update(job_id, "running", "Loading site data")
            site_df = fetch_site_data(project_id)

            detected_operator = self._detect_operator(site_df)
            operator = requested_operator or detected_operator or "Airtel"

            self._update(job_id, "running", "Calculating K1/K2")
            k1k2_map = compute_k1k2(baseline_df, site_df)

            self._update(job_id, "running", f"Loading optimized sites for {operator}")
            opt_sites = fetch_optimized_sites(project_id, operator)
            if opt_sites.empty:
                raise ValueError(f"No optimized sites found for operator '{operator}'")

            params = {
                "radius": float(cfg.get("radius", cfg.get("radius_m", 5000))),
                "grid_resolution": float(cfg.get("grid_resolution", cfg.get("grid_value", 25))),
                "n_workers": cfg.get("n_workers"),
                "antenna_gain": 18,
                "cable_loss": 2,
                "ue_height": 1.5,
                "frequency_mhz": 1800,
                "bandwidth_mhz": 10,
            }

            self._update(job_id, "running", "Running prediction")
            optimized_df = run_prediction_only_optimized(opt_sites, k1k2_map, params)

            if optimized_df is None or optimized_df.empty:
                raise ValueError("Optimized prediction produced no rows")

            self._update(job_id, "running", "Saving CSV")
            file_path = self._save_csv(optimized_df, project_id, operator)

            db_df = self._format_for_db(optimized_df, project_id, job_id, operator)
            inserted = self._save_to_db(project_id, job_id, db_df)

            JOBS[job_id]["output"] = file_path
            JOBS[job_id]["rows"] = int(len(optimized_df))
            JOBS[job_id]["inserted"] = int(inserted)
            JOBS[job_id]["operator"] = operator
            self._update(job_id, "done", "Completed")
        except Exception as exc:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = str(exc)
            print("ERROR:", traceback.format_exc())

    def _save_csv(self, df, project_id, operator):
        output_dir = "outputs"
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join(output_dir, f"optimized_{operator}_{project_id}_{timestamp}.csv")
        df.to_csv(file_path, index=False)
        return file_path

    def _save_to_db(self, project_id, job_id, df):
        if df.empty:
            return 0

        if USE_BACKEND_PROXY:
            return int(save_lte_prediction_optimised_results(int(project_id), str(job_id), df))

        if engine is None:
            raise RuntimeError("DATABASE_URL not configured for direct DB mode")

        df.to_sql(
            "lte_prediction_optimised_results",
            con=engine,
            if_exists="append",
            index=False,
            chunksize=15000,
            method="multi",
        )
        return int(len(df))

    def _format_for_db(self, df, project_id, job_id, operator):
        out = df.copy()

        if "Node_Cell_ID" not in out.columns:
            raise ValueError("Invalid optimized output: Node_Cell_ID column missing")

        split_cols = out["Node_Cell_ID"].astype(str).str.split("_", n=1, expand=True)
        out["node_b_id"] = split_cols[0].astype(str).str.strip()
        out["cell_id"] = split_cols[1].astype(str).str.strip() if split_cols.shape[1] > 1 else ""
        out["nodeb_id_cell_id"] = out["Node_Cell_ID"].astype(str).str.strip()

        out["project_id"] = int(project_id)
        out["job_id"] = str(job_id)
        out["operator"] = operator
        out["created_at"] = datetime.datetime.utcnow()
        out["site_id"] = out["node_b_id"]

        for col in ("lat", "lon", "pred_rsrp", "pred_rsrq", "pred_sinr"):
            if col not in out.columns:
                out[col] = None

        final_df = out[
            [
                "project_id",
                "job_id",
                "lat",
                "lon",
                "pred_rsrp",
                "pred_rsrq",
                "pred_sinr",
                "node_b_id",
                "cell_id",
                "operator",
                "created_at",
                "site_id",
                "nodeb_id_cell_id",
            ]
        ].copy()

        return final_df

    def _detect_operator(self, site_df):
        if site_df is None or site_df.empty:
            return None
        for candidate in ("cluster", "cluster_name", "operator", "provider", "m_alpha_long", "m_alpha_short"):
            if candidate in site_df.columns:
                value = site_df[candidate].dropna()
                if not value.empty:
                    return self._normalize_operator(value.iloc[0])
        return None

    def _normalize_operator(self, value):
        if value is None:
            return None
        parsed = str(value).strip()
        return parsed or None

    def _update(self, job_id, status, msg):
        JOBS[job_id]["status"] = status
        JOBS[job_id]["progress"] = msg
        print(f"[{job_id[:6]}] {msg}")
