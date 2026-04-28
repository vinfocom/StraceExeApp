from flask import Blueprint, request, jsonify, send_file
from .services import LTEPredictionService_optimised

lte_prediction_op = Blueprint("lte_prediction_optimized", __name__)

service = LTEPredictionService_optimised()


# ==========================================================
# RUN OPTIMIZED PREDICTION
# ==========================================================
@lte_prediction_op.route("/run", methods=["POST"])
@lte_prediction_op.route("/optimized", methods=["POST"])
def run_optimized():
    data = request.get_json() or {}

    project_id = data.get("project_id")
    if project_id is None:
        return jsonify({"error": "project_id is required"}), 400

    cfg = {
        "project_id": project_id,
        "operator": data.get("operator"),
        "radius": data.get("radius", data.get("radius_m", 5000)),
        "grid_resolution": data.get("grid_resolution", data.get("grid_value", 25)),
        "n_workers": data.get("n_workers"),
        "user_id": data.get("user_id"),
    }

    result = service.submit(cfg)
    return jsonify(result)


# ==========================================================
# CHECK STATUS
# ==========================================================
@lte_prediction_op.route("/status/<job_id>", methods=["GET"])
def status(job_id):

    job = service.get(job_id)

    if not job:
        return jsonify({"error": "Job not found"}), 404

    return jsonify(job)


# ==========================================================
# DOWNLOAD FILE
# ==========================================================
@lte_prediction_op.route("/download", methods=["GET"])
def download():

    file_path = request.args.get("file")

    if not file_path:
        return jsonify({"error": "file path required"}), 400

    return send_file(file_path, as_attachment=True)
