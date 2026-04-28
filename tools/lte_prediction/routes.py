from flask import Blueprint, request, jsonify
from .services import LTEPredictionService
import multiprocessing
lte_prediction_bp = Blueprint("lte_prediction", __name__)
svc = LTEPredictionService()


@lte_prediction_bp.route("/run", methods=["POST"])
def run_prediction():

    try:
        data = request.get_json() or {}
        if "project_id" not in data or "session_ids" not in data:
            return jsonify({"error": "project_id and session_ids are required"}), 400
        user_id = int(data.get("user_id") or data.get("User_id") or 0)
        if user_id <= 0:
            return jsonify({"error": "user_id is required"}), 400

        cpu_count = multiprocessing.cpu_count()
        project_id = int(data["project_id"])
        session_ids = data["session_ids"]
        if not isinstance(session_ids, list) or len(session_ids) == 0:
            return jsonify({"error": "session_ids must be a non-empty list"}), 400

        radius_m = data.get("radius_m", data.get("radius", 500))
        grid_resolution = data.get("grid_resolution", data.get("grid_value", 25))

        cfg = {
            "project_id": project_id,
            "session_ids": session_ids,
            "radius_m": float(radius_m),
            "grid_resolution": float(grid_resolution),
            "building": bool(data.get("building", True)),
            "n_workers": int(data.get("n_workers", max(1, cpu_count - 1))),
            "polygon_area": data.get("polygon_area"),
        }

        return jsonify(svc.submit(cfg))

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@lte_prediction_bp.route("/status/<job_id>", methods=["GET"])
def status(job_id):
    return jsonify(svc.get(job_id))


@lte_prediction_bp.route("/result/<job_id>", methods=["GET"])
def result(job_id):
    return jsonify(svc.get(job_id))
