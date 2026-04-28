from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import bindparam, text

from extensions import db
from utils.signaltrackers_client import (
    backend_db_mode_enabled,
    fetch_available_polygons as fetch_available_polygons_remote,
    fetch_projects as fetch_projects_remote,
)


local_mapview_bp = Blueprint("local_mapview", __name__)


def _normalize_ids(values):
    if values is None:
        return []

    if not isinstance(values, (list, tuple, set)):
        values = [values]

    parsed = []
    seen = set()

    for value in values:
        if value is None:
            continue
        token = str(value).strip()
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        parsed.append(token)

    return parsed


def _parse_csv_ids(csv_value):
    if csv_value is None:
        return []
    return _normalize_ids(str(csv_value).split(","))


def _to_int_ids(values):
    parsed = []
    for token in _normalize_ids(values):
        try:
            parsed.append(int(token))
        except (TypeError, ValueError):
            continue
    return parsed


def _rows_to_polygon_payload(rows):
    data = []
    for row in rows:
        row = row or {}
        polygon_id = row.get("id") or row.get("Id")
        name = row.get("name") or row.get("Name")
        wkt = (
            row.get("wkt")
            or row.get("WKT")
            or row.get("region_wkt")
            or row.get("RegionWkt")
        )
        session_csv = row.get("session_id") or row.get("SessionId")
        session_ids = row.get("sessionIds") or row.get("SessionIds")
        if not isinstance(session_ids, list):
            session_ids = _parse_csv_ids(session_ids or session_csv)

        data.append(
            {
                "id": polygon_id,
                "name": name,
                "wkt": wkt,
                "session_id": session_csv,
                "sessionIds": session_ids,
                "area": row.get("area") or row.get("Area"),
                "tbl_project_id": row.get("tbl_project_id") or row.get("TblProjectId"),
            }
        )
    return data


@local_mapview_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "local-mapview"}), 200


@local_mapview_bp.route("/available-polygons", methods=["GET"])
def get_available_polygons():
    project_id = request.args.get("projectId", type=int)
    company_id = request.args.get("company_id", type=int)

    if backend_db_mode_enabled():
        rows = fetch_available_polygons_remote(project_id=project_id, company_id=company_id)
        payload = _rows_to_polygon_payload(rows)
        return (
            jsonify(
                {
                    "Status": 1,
                    "Message": "Success",
                    "count": len(payload),
                    "data": payload,
                    "Data": payload,
                }
            ),
            200,
        )

    where = ["status = 1"]
    params = {}

    if project_id:
        where.append("tbl_project_id = :project_id")
        params["project_id"] = project_id
    else:
        where.append("(tbl_project_id IS NULL OR tbl_project_id = 0)")

    query = text(
        f"""
        SELECT
            id,
            name,
            ST_AsText(region) AS wkt,
            session_id,
            area,
            tbl_project_id
        FROM map_regions
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
        """
    )

    rows = db.session.execute(query, params).mappings().all()
    payload = _rows_to_polygon_payload(rows)

    return (
        jsonify(
            {
                "Status": 1,
                "Message": "Success",
                "count": len(payload),
                "data": payload,
                "Data": payload,
            }
        ),
        200,
    )


@local_mapview_bp.route("/projects", methods=["GET"])
def get_projects():
    company_id = request.args.get("company_id", type=int)

    if backend_db_mode_enabled():
        projects = fetch_projects_remote(company_id=company_id)
        return jsonify({"Status": 1, "Message": "Success", "Data": projects}), 200

    where = ["(p.status IS NULL OR p.status <> 0)"]
    params = {}

    if company_id is not None:
        where.append("p.company_id = :company_id")
        params["company_id"] = company_id

    query = text(
        f"""
        SELECT
            p.id,
            p.project_name,
            p.ref_session_id,
            p.from_date,
            p.to_date,
            p.provider,
            p.tech,
            p.band,
            p.earfcn,
            p.apps,
            p.created_on,
            p.ended_on,
            p.status,
            p.company_id,
            p.Download_path,
            p.grid_size,
            ST_AsText(p.polygon) AS project_polygon_wkt,
            mr.region_wkt,
            mr.region_blob_b64,
            COALESCE(ST_AsText(p.polygon), mr.region_wkt) AS geometry_wkt
        FROM tbl_project p
        LEFT JOIN (
            SELECT
                mr1.tbl_project_id,
                ST_AsText(mr1.region) AS region_wkt,
                TO_BASE64(mr1.region) AS region_blob_b64
            FROM map_regions mr1
            INNER JOIN (
                SELECT tbl_project_id, MAX(id) AS max_id
                FROM map_regions
                WHERE tbl_project_id IS NOT NULL
                GROUP BY tbl_project_id
            ) picked
              ON picked.tbl_project_id = mr1.tbl_project_id
             AND picked.max_id = mr1.id
        ) mr
          ON mr.tbl_project_id = p.id
        WHERE {' AND '.join(where)}
        ORDER BY p.id DESC
        """
    )

    projects = [dict(row) for row in db.session.execute(query, params).mappings().all()]

    return jsonify({"Status": 1, "Message": "Success", "Data": projects}), 200


@local_mapview_bp.route("/projects/create-with-polygons", methods=["POST"])
def create_project_with_polygons():
    payload = request.get_json(silent=True) or {}

    project_name = (payload.get("ProjectName") or payload.get("project_name") or "").strip()
    polygon_ids = _to_int_ids(payload.get("PolygonIds") or payload.get("polygon_ids") or [])
    requested_sessions = _normalize_ids(
        payload.get("SessionIds")
        or payload.get("session_ids")
        or payload.get("ref_session_ids")
        or []
    )
    raw_grid_size = (
        payload.get("GridSize")
        or payload.get("grid_size")
        or payload.get("gridSize")
        or payload.get("grid")
    )
    grid_size = None
    try:
        if raw_grid_size not in (None, ""):
            parsed_grid = float(raw_grid_size)
            if parsed_grid > 0:
                grid_size = str(parsed_grid)
    except (TypeError, ValueError):
        grid_size = None

    if not project_name:
        return jsonify({"Status": 0, "Message": "ProjectName is required"}), 400

    if not polygon_ids:
        return jsonify({"Status": 0, "Message": "PolygonIds is required"}), 400

    polygon_query = text(
        """
        SELECT id, name, session_id, ST_AsText(region) AS wkt
        FROM map_regions
        WHERE id IN :polygon_ids AND status = 1
        """
    ).bindparams(bindparam("polygon_ids", expanding=True))

    polygon_rows = db.session.execute(
        polygon_query,
        {"polygon_ids": polygon_ids},
    ).mappings().all()

    if not polygon_rows:
        return jsonify({"Status": 0, "Message": "No valid polygons found"}), 404

    resolved_sessions = list(requested_sessions)
    if not resolved_sessions:
        for row in polygon_rows:
            for sid in _parse_csv_ids(row.get("session_id")):
                if sid not in resolved_sessions:
                    resolved_sessions.append(sid)

    if not resolved_sessions:
        return (
            jsonify(
                {
                    "Status": 0,
                    "Message": "No session IDs found. Select sessions or use polygons with linked sessions.",
                }
            ),
            400,
        )

    session_range = None
    try:
        range_query = text(
            """
            SELECT MIN(start_time) AS from_dt, MAX(end_time) AS to_dt
            FROM tbl_session
            WHERE id IN :session_ids
            """
        ).bindparams(bindparam("session_ids", expanding=True))
        session_range = db.session.execute(
            range_query,
            {"session_ids": _to_int_ids(resolved_sessions)},
        ).mappings().first()
    except Exception:
        session_range = None

    from_date = None
    to_date = None
    if session_range:
        from_date = session_range.get("from_dt")
        to_date = session_range.get("to_dt")

    created_on = datetime.utcnow()
    company_id = int(payload.get("CompanyId") or payload.get("company_id") or 0)
    ref_session_csv = ",".join(resolved_sessions)
    polygon_wkt = polygon_rows[0].get("wkt")

    try:
        if polygon_wkt:
            insert_stmt = text(
                """
                INSERT INTO tbl_project
                (
                    project_name,
                    ref_session_id,
                    from_date,
                    to_date,
                    polygon,
                    created_on,
                    grid_size,
                    status,
                    company_id
                )
                VALUES
                (
                    :project_name,
                    :ref_session_id,
                    :from_date,
                    :to_date,
                    ST_GeomFromText(:polygon_wkt),
                    :created_on,
                    :grid_size,
                    1,
                    :company_id
                )
                """
            )
        else:
            insert_stmt = text(
                """
                INSERT INTO tbl_project
                (
                    project_name,
                    ref_session_id,
                    from_date,
                    to_date,
                    polygon,
                    created_on,
                    grid_size,
                    status,
                    company_id
                )
                VALUES
                (
                    :project_name,
                    :ref_session_id,
                    :from_date,
                    :to_date,
                    NULL,
                    :created_on,
                    :grid_size,
                    1,
                    :company_id
                )
                """
            )

        params = {
            "project_name": project_name,
            "ref_session_id": ref_session_csv,
            "from_date": str(from_date) if from_date else None,
            "to_date": str(to_date) if to_date else None,
            "polygon_wkt": polygon_wkt,
            "created_on": created_on,
            "grid_size": grid_size,
            "company_id": company_id,
        }

        result = db.session.execute(insert_stmt, params)
        project_id = int(result.lastrowid)

        update_regions_stmt = text(
            "UPDATE map_regions SET tbl_project_id = :project_id WHERE id IN :polygon_ids"
        ).bindparams(bindparam("polygon_ids", expanding=True))

        db.session.execute(
            update_regions_stmt,
            {
                "project_id": project_id,
                "polygon_ids": polygon_ids,
            },
        )

        project_row = db.session.execute(
            text(
                """
                SELECT
                    id,
                    project_name,
                    ref_session_id,
                    from_date,
                    to_date,
                    created_on,
                    status,
                    company_id,
                    grid_size
                FROM tbl_project
                WHERE id = :project_id
                """
            ),
            {"project_id": project_id},
        ).mappings().first()

        db.session.commit()

        response_project = dict(project_row) if project_row else {
            "id": project_id,
            "project_name": project_name,
            "ref_session_id": ref_session_csv,
            "created_on": created_on,
            "status": 1,
            "company_id": company_id,
            "grid_size": grid_size,
        }

        return (
            jsonify(
                {
                    "Status": 1,
                    "Message": "Project created successfully",
                    "Data": {
                        "projectId": project_id,
                        "project_id": project_id,
                        "id": project_id,
                        "project": response_project,
                        "polygonIds": polygon_ids,
                        "sessionIds": resolved_sessions,
                    },
                }
            ),
            200,
        )
    except Exception as exc:
        db.session.rollback()
        return (
            jsonify(
                {
                    "Status": 0,
                    "Message": "Failed to create project",
                    "InnerException": str(exc),
                }
            ),
            500,
        )


@local_mapview_bp.route("/projects/<int:project_id>", methods=["DELETE"])
def delete_project(project_id):
    try:
        deleted = db.session.execute(
            text(
                """
                UPDATE tbl_project
                SET status = 0, ended_on = :ended_on
                WHERE id = :project_id
                """
            ),
            {"project_id": project_id, "ended_on": datetime.utcnow()},
        )

        if deleted.rowcount == 0:
            db.session.rollback()
            return jsonify({"Status": 0, "Message": "Project not found"}), 404

        db.session.execute(
            text("UPDATE map_regions SET tbl_project_id = NULL WHERE tbl_project_id = :project_id"),
            {"project_id": project_id},
        )

        db.session.commit()
        return jsonify({"Status": 1, "Message": "Project deleted successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"Status": 0, "Message": str(exc)}), 500


@local_mapview_bp.route("/project-polygons", methods=["GET"])
def get_project_polygons():
    project_id = request.args.get("projectId", type=int)
    if not project_id:
        return jsonify({"Status": 0, "Message": "projectId is required"}), 400

    rows = db.session.execute(
        text(
            """
            SELECT
                id,
                name,
                ST_AsText(region) AS wkt,
                session_id,
                area,
                tbl_project_id
            FROM map_regions
            WHERE tbl_project_id = :project_id AND status = 1
            ORDER BY id DESC
            """
        ),
        {"project_id": project_id},
    ).mappings().all()

    data = _rows_to_polygon_payload(rows)
    return jsonify({"Status": 1, "Data": data, "data": data, "count": len(data)}), 200
