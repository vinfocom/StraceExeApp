import os
import logging
import sys
from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException


def _configure_console_streams():
    """Avoid Windows cp1252 crashes when background jobs print Unicode."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except Exception:
                pass


_configure_console_streams()

# Always load python/.env regardless of current working directory (important when launched from electron/)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
# Force backend-proxy mode unless explicitly overridden.
os.environ.setdefault("DB_ACCESS_MODE", "backend")

# Import config, blueprints, and db
from config import config
from tools.buildings.routes import buildings_bp
from tools.cell_site.routes import cell_site_bp
from tools.prediction.routes import prediction_bp
# -------------------------------------------------
# 1. IMPORT THE NEW BLUEPRINT
# -------------------------------------------------
from tools.area_breakup.routes import area_breakup_bp
from tools.report.routes import report_bp
from tools.lte_prediction.routes import lte_prediction_bp
from tools.lte_prediction_optimised.routes import lte_prediction_op
from tools.lte_tilt_recommandation.routes import lte_tilt_recommendation_bp
from tools.local_mapview.routes import local_mapview_bp

from extensions import db
from flask_migrate import Migrate

# Migration object
migrate = Migrate()


def create_app(config_name='default'):
    """
    Flask Application Factory
    """
    app = Flask(__name__)

    # -------------------------------------------------------------------
    # LOGGING CONFIG
    # -------------------------------------------------------------------
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
    app.logger.setLevel(getattr(logging, log_level, logging.INFO))

    # Silence noisy third-party debug logs
    for noisy in [
        "botocore",
        "boto3",
        "s3transfer",
        "httpx",
        "urllib3",
        "matplotlib",
        "PIL",
        "groq",
        "asyncio",
    ]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # -------------------------------------------------------------------
    # LOAD CONFIG
    # -------------------------------------------------------------------
    env_config = config.get(config_name, config['default'])
    app.config.from_object(env_config)

    # File upload limits
    app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

    # Keep runtime files in writable folders (critical for packaged desktop apps).
    upload_folder = (
        os.getenv("UPLOAD_FOLDER")
        or os.getenv("PYTHON_UPLOAD_FOLDER")
        or app.config.get("UPLOAD_FOLDER")
        or os.path.join(app.config.get("RUNTIME_ROOT", os.path.dirname(__file__)), 'uploads')
    )
    output_folder = (
        os.getenv("OUTPUT_FOLDER")
        or os.getenv("PYTHON_OUTPUT_FOLDER")
        or app.config.get("OUTPUT_FOLDER")
        or os.path.join(app.config.get("RUNTIME_ROOT", os.path.dirname(__file__)), 'outputs')
    )
    app.config['UPLOAD_FOLDER'] = upload_folder
    app.config['OUTPUT_FOLDER'] = output_folder

    # Create directories if missing
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

    # Config init hook
    if hasattr(env_config, 'init_app'):
        env_config.init_app()

    # -------------------------------------------------------------------
    # INIT EXTENSIONS
    # -------------------------------------------------------------------
    db.init_app(app)
    migrate.init_app(app, db)

    # -------------------------------------------------------------------
    # GLOBAL CORS
    # -------------------------------------------------------------------
    CORS(app,
         origins=["*", "http://localhost:5173", "https://singnaltracker.netlify.app"],
         methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
         allow_headers=["Content-Type", "Authorization", "Accept"],
         supports_credentials=True,
         max_age=3600
    )

    # -------------------------------------------------------------------
    # BLUEPRINTS
    # -------------------------------------------------------------------
    app.register_blueprint(buildings_bp, url_prefix='/api/buildings')
    app.register_blueprint(cell_site_bp, url_prefix='/api/cell-site')
    app.register_blueprint(prediction_bp, url_prefix='/api/prediction')
    
    # -------------------------------------------------
    # 2. REGISTER THE NEW BLUEPRINT
    # -------------------------------------------------
    app.register_blueprint(area_breakup_bp, url_prefix='/api/area-breakup')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    app.register_blueprint(lte_prediction_bp, url_prefix="/api/lte-prediction")
    app.register_blueprint(lte_prediction_op, url_prefix="/api/lte-prediction-optimised")
    app.register_blueprint(lte_tilt_recommendation_bp, url_prefix="/api/lte-tilt-recommendation")
    app.register_blueprint(local_mapview_bp, url_prefix="/api/local-mapview")


    # -------------------------------------------------------------------
    # ROOT ENDPOINTS
    # -------------------------------------------------------------------
    @app.route('/', methods=['GET'])
    def root():
        return jsonify({
            "message": "Python ML Backend is running",
            "services": {
                "buildings": "/api/buildings",
                "cell_site": "/api/cell-site",
                "prediction": "/api/prediction",
                "area_breakup": "/api/area-breakup",
                "report": "/api/report",
                "site_prediction": "/api/lte-prediction/run",
                "optimized_prediction": "/api/lte-prediction-optimised/run",
                "lte_tilt_recommendation": "/api/lte-tilt-recommendation/optimize",
                "local_mapview": "/api/local-mapview",
            }
        })

    @app.route('/health', methods=['GET'])
    def health_check():
        return jsonify({
            'status': 'healthy',
            'service': 'Python ML Backend',
            'message': 'Service is running!'
        }), 200

    # -------------------------------------------------------------------
    # ERROR HANDLERS (Keep your existing handlers here)
    # -------------------------------------------------------------------
    @app.errorhandler(413)
    def request_entity_too_large(error):
        app.logger.error(f"File too large: {error}")
        return jsonify({'error': 'File too large. Maximum size is 100MB'}), 413

    @app.errorhandler(500)
    def internal_error(error):
        app.logger.error(f"Internal error: {error}")
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500

    @app.errorhandler(Exception)
    def handle_exception(e):
        if isinstance(e, HTTPException):
            return jsonify({
                "error": e.name,
                "message": e.description,
            }), e.code

        app.logger.error(f"Unhandled exception: {str(e)}", exc_info=True)
        # Only rollback if db session is active
        try:
            db.session.rollback()
        except:
            pass
            
        return jsonify({
            'error': 'Internal server error',
            'message': str(e),
            'type': type(e).__name__
        }), 500

    return app


# -------------------------------------------------------------------
# APP ENTRY POINT
# -------------------------------------------------------------------
app_env = os.getenv('FLASK_ENV', 'default')
app = create_app(app_env)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))

    app.run(
        host=os.getenv('HOST', '0.0.0.0'),
        port=port,
        debug=app.config.get('DEBUG', False),
        use_reloader=False
    )
