from flask import Blueprint, request, jsonify, current_app
import os
import uuid
import traceback
import threading
from sqlalchemy import text
from extensions import db
from tools.prediction.services import run_prediction_pipeline
from utils.signaltrackers_client import (
    backend_db_mode_enabled,
    prediction_debug_summary,
)

prediction_bp = Blueprint("prediction", __name__)


def _persist_project_grid_size(project_id, pixel_size):
    if not project_id:
        return
    if backend_db_mode_enabled():
        # In backend-proxy mode Python must not write directly to DB.
        return

    try:
        numeric_grid = float(pixel_size)
    except (TypeError, ValueError):
        return

    if numeric_grid <= 0:
        return

    try:
        db.session.execute(
            text(
                """
                UPDATE tbl_project
                SET grid_size = :grid_size
                WHERE id = :project_id
                """
            ),
            {
                "project_id": int(project_id),
                "grid_size": str(numeric_grid),
            },
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.warning(
            "[Prediction] Could not persist grid_size for project %s: %s",
            project_id,
            exc,
        )

# =======================================================
# BACKGROUND TASK WRAPPER
# =======================================================
def background_prediction_task(app, project_id, session_ids, run_dir, indoor_mode, pixel_size):
    """
    Runs the heavy prediction pipeline inside a separate thread
    so the web request doesn't timeout.
    """
    with app.app_context():
        try:
            print(f"--- [Background] Starting Prediction for Project {project_id} ---")

            if not backend_db_mode_enabled():
                raise RuntimeError(
                    "DB_ACCESS_MODE must be 'backend' for prediction routes. "
                    "Direct DB mode is disabled for this deployment."
                )

            out_dir, count = run_prediction_pipeline(
                db_connection=None,
                project_id=str(project_id),
                session_ids=[str(s) for s in session_ids],
                outdir=run_dir,
                indoor_mode=indoor_mode,
                pixel_size_meters=pixel_size,
            )
            
            print(f"--- [Background] Success! Project {project_id}: {count} rows written. ---")
            
        except Exception as e:
            print(f"--- [Background] FAILED Project {project_id} ---")
            print(traceback.format_exc())

# =======================================================
# RUN PREDICTION (ASYNC)
# =======================================================

@prediction_bp.route("/run", methods=["POST"])
def run_prediction():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    project_id = data.get("Project_id")
    session_ids = data.get("Session_ids")
    indoor_mode = data.get("indoor_mode", "heuristic")

    try:
        pixel_size = float(data.get("grid", 22.0))
    except:
        pixel_size = 22.0

    if not project_id or not session_ids:
        return jsonify({"error": "Project_id and Session_ids required"}), 400

    _persist_project_grid_size(project_id, pixel_size)

    # Setup Paths
    output_root = current_app.config.get(
        "OUTPUT_FOLDER",
        os.path.join(os.getcwd(), "outputs")
    )
    run_id = str(uuid.uuid4())
    run_dir = os.path.join(output_root, f"lte_run_{run_id}")

    # Capture the real app object to pass to the thread
    app = current_app._get_current_object()

    # Start the background thread
    thread = threading.Thread(
        target=background_prediction_task,
        args=(app, project_id, session_ids, run_dir, indoor_mode, pixel_size)
    )
    thread.start()

    # RETURN IMMEDIATELY (202 Accepted)
    return jsonify({
        "message": "Prediction started in background.",
        "status": "processing",
        "project_id": project_id,
        "run_id": run_id,
        "note": "Check your map/database in 2-3 minutes."
    }), 202


# =======================================================
# DEBUG DB
# =======================================================

@prediction_bp.route("/debug-db/<int:project_id>", methods=["GET"])
def debug_database(project_id):
    try:
        if backend_db_mode_enabled():
            results = prediction_debug_summary(project_id)
            return jsonify(results), 200

        results = {}
        with db.engine.connect() as conn:
            tables = conn.execute(text("SHOW TABLES")).fetchall()
            results["tables"] = [t[0] for t in tables]

            proj = conn.execute(
                text("SELECT * FROM tbl_project WHERE id=:project_id"),
                {"project_id": project_id},
            ).fetchone()
            results["project_exists"] = bool(proj)

            try:
                cnt = conn.execute(
                    text("SELECT COUNT(*) FROM site_noMl WHERE project_id=:project_id"),
                    {"project_id": project_id},
                ).scalar()
                results["site_noMl_count"] = cnt
            except Exception as e:
                results["site_noMl_error"] = str(e)

        return jsonify(results), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
