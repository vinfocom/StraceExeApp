import uuid
import threading
import os
import pandas as pd

from .ml_engine import (
    run_rf_prediction_fast,
    run_ml_fast,
    fetch_site_data,
    fetch_drive_data,
    fetch_building_data
)
from datetime import datetime
from extensions import db
from utils.signaltrackers_client import (
    backend_db_mode_enabled,
    save_lte_prediction_results,
)

JOBS = {}


class LTEPredictionService:

    def submit(self, cfg):

        job_id = str(uuid.uuid4())

        JOBS[job_id] = {"status": "queued"}

        threading.Thread(
            target=self._run,
            args=(job_id, cfg),
            daemon=True
        ).start()

        return {"job_id": job_id}

    def get(self, job_id):
        return JOBS.get(job_id)

    def _run(self, job_id, cfg):

        try:
            self._update(job_id, "running", "Fetching site data")

            # ✅ STEP 1: SITE + OPERATOR
            site_df, operator = fetch_site_data(cfg["project_id"])

            self._update(job_id, "running", f"Operator: {operator}")

            # ✅ STEP 2: DRIVE DATA (FILTERED + CACHE)
            self._update(job_id, "running", "Fetching drive data")

            drive_df = fetch_drive_data(cfg["session_ids"], operator)

            # ✅ STEP 3: BUILDING DATA
            self._update(job_id, "running", "Fetching building data")

            building_df = fetch_building_data(cfg["project_id"])

            # 🚀 RF PREDICTION
            self._update(job_id, "running", "RF Prediction")

            pred_df = run_rf_prediction_fast(
                site_df,
                drive_df,
                building_df,
                {
                    "radius": cfg["radius_m"],
                    "grid": cfg["grid_resolution"],
                    "workers": cfg["n_workers"],
                    "polygon_area": cfg.get("polygon_area"),
                }
            )

            # 🧠 ML CORRECTION
            self._update(job_id, "running", "ML Correction")

            final_df = run_ml_fast(pred_df, drive_df)

            inserted_rows = self._save_baseline_results(
                final_df,
                cfg["project_id"],
                job_id,
            )

            # 💾 SAVE OUTPUT (TEMP)
            output = f"temp/final_{job_id}.csv"
            os.makedirs("temp", exist_ok=True)
            final_df.to_csv(output, index=False)

            JOBS[job_id]["output"] = output
            JOBS[job_id]["rows"] = len(final_df)
            JOBS[job_id]["inserted"] = inserted_rows

            self._update(job_id, "done", "Completed")

        except Exception as e:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = str(e)

    def _update(self, job_id, status, msg):
        JOBS[job_id]["status"] = status
        JOBS[job_id]["progress"] = msg
        print(f"[{job_id[:6]}] {msg}")

    def _save_baseline_results(self, df, project_id, job_id):

        print("💾 Saving baseline results to DB...")

        # ✅ COPY DATA
        out = df.copy()
        out.columns = (
            out.columns.astype(str)
            .str.strip()
            .str.lower()
            .str.replace(" ", "_")
        )

        rename_map = {
            "latitude": "lat",
            "longitude": "lon",
        }
        out = out.rename(columns=rename_map)

        if "site_id" not in out.columns:
            out["site_id"] = None

        required_cols = [
            "lat",
            "lon",
            "pred_rsrp",
            "pred_rsrq",
            "pred_sinr",
            "site_id",
        ]
        for column in required_cols:
            if column not in out.columns:
                out[column] = None

        out = out[required_cols]
        out = out.dropna(subset=["lat", "lon"]).copy()

        if out.empty:
            print("⚠️ No valid LTE baseline rows to save.")
            return 0

        if backend_db_mode_enabled():
            inserted = save_lte_prediction_results(project_id, job_id, out)
            print(f"✅ {inserted} rows inserted into tbl_lte_prediction_results via bridge")
            return inserted

        direct_out = out.copy()
        direct_out["project_id"] = project_id
        direct_out["job_id"] = job_id
        direct_out["created_at"] = datetime.now()
        direct_out = direct_out[
            [
                "job_id",
                "lat",
                "lon",
                "pred_rsrp",
                "pred_rsrq",
                "pred_sinr",
                "project_id",
                "site_id",
                "created_at",
            ]
        ]

        direct_out.to_sql(
            "tbl_lte_prediction_results",
            db.engine,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=5000,
        )

        print(f"✅ {len(direct_out)} rows inserted into tbl_lte_prediction_results")
        return len(direct_out)
