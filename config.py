import os
import tempfile
import sys
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))


def _default_runtime_root() -> str:
    explicit_root = (os.getenv("S_TRACER_RUNTIME_ROOT") or "").strip()
    if explicit_root:
        return explicit_root

    if os.getenv("RENDER"):
        return os.path.join(tempfile.gettempdir(), "s-tracer-runtime")

    if getattr(sys, "frozen", False):
        return os.path.join(tempfile.gettempdir(), "s-tracer-runtime")

    return os.path.dirname(os.path.abspath(__file__))


def get_runtime_root() -> str:
    return _default_runtime_root()


def get_reports_root() -> str:
    return os.path.join(get_runtime_root(), "reports")


class Config:
    # ---------------------------------------------------
    # FLASK BASIC CONFIG
    # ---------------------------------------------------
    SECRET_KEY = os.getenv('SECRET_KEY', os.urandom(24).hex())
    DEBUG = False
    TESTING = False

    # Server
    PORT = int(os.getenv('PORT', 8080))

    # Base directory
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RUNTIME_ROOT = get_runtime_root()
    REPORTS_FOLDER = get_reports_root()

    # ---------------------------------------------------
    # UPLOAD / OUTPUT DIRECTORY HANDLING
    # ---------------------------------------------------
    UPLOAD_FOLDER = (
        os.getenv("UPLOAD_FOLDER")
        or os.getenv("PYTHON_UPLOAD_FOLDER")
        or os.path.join(RUNTIME_ROOT, "uploads")
    )
    OUTPUT_FOLDER = (
        os.getenv("OUTPUT_FOLDER")
        or os.getenv("PYTHON_OUTPUT_FOLDER")
        or os.path.join(RUNTIME_ROOT, "outputs")
    )

    MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH', 100 * 1024 * 1024))
    ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls', 'geojson', 'json'}

    # ---------------------------------------------------
    # CORS
    # ---------------------------------------------------
    CORS_ORIGINS = os.getenv('CORS_ORIGINS', '*').split(',')

    # ---------------------------------------------------
    # STORAGE SETTINGS (Optional)
    # ---------------------------------------------------
    USE_S3 = os.getenv('USE_S3', 'false').lower() == 'true'
    AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
    S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
    S3_REGION = os.getenv('S3_REGION', 'us-east-1')
    CLOUDINARY_URL = os.getenv('CLOUDINARY_URL')

    # ---------------------------------------------------
    # DATABASE ACCESS MODE
    # direct  -> Python connects DB using DATABASE_URL
    # backend -> Python reads/writes through Signal-Trackers API
    # ---------------------------------------------------
    DB_ACCESS_MODE = os.getenv("DB_ACCESS_MODE", "backend").strip().lower()
    USE_BACKEND_DB_PROXY = DB_ACCESS_MODE == "backend"

    # Keep SQLAlchemy initialized even in backend mode (safe local fallback),
    # so routes that still import db don't crash at startup.
    SQLALCHEMY_DATABASE_URI = (
        os.getenv("SQLALCHEMY_FALLBACK_URI", f"sqlite:///{os.path.join(BASE_DIR, 'local_fallback.db')}")
        if USE_BACKEND_DB_PROXY
        else os.getenv("DATABASE_URL")
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 🔥 FIXED: This prevents MySQL timeouts (your main issue)
    SQLALCHEMY_ENGINE_OPTIONS = (
        {}
        if USE_BACKEND_DB_PROXY
        else {
            "pool_pre_ping": True,     # auto-reconnect if connection is dead
            "pool_recycle": 280,       # recycle before MySQL 300s timeout
            "pool_size": 10,           # recommended size
            "max_overflow": 20,        # extra temp connections
        }
    )

    # ---------------------------------------------------
    # TOOL-SPECIFIC SETTINGS
    # ---------------------------------------------------
    CELL_SITE_MIN_SAMPLES = int(os.getenv('CELL_SITE_MIN_SAMPLES', 30))
    CELL_SITE_BIN_SIZE = int(os.getenv('CELL_SITE_BIN_SIZE', 5))

    @staticmethod
    def init_app():
        """Ensure required folders exist."""
        upload_folder = (
            os.getenv("UPLOAD_FOLDER")
            or os.getenv("PYTHON_UPLOAD_FOLDER")
            or Config.UPLOAD_FOLDER
        )
        output_folder = (
            os.getenv("OUTPUT_FOLDER")
            or os.getenv("PYTHON_OUTPUT_FOLDER")
            or Config.OUTPUT_FOLDER
        )

        for folder in [upload_folder, output_folder]:
            os.makedirs(folder, exist_ok=True)
            # Keep startup logs ASCII-safe for Windows default console encodings.
            print(f"Created directory: {folder}")


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class RenderConfig(ProductionConfig):
    """Production config for Render.com"""
    pass


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'render':       RenderConfig,
    'default':      DevelopmentConfig
}
