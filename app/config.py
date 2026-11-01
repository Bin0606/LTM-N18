"""Cấu hình ứng dụng. Mọi giá trị nhạy cảm đọc từ biến môi trường (xem .env.example)."""
import os
import secrets
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _database_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return "sqlite:///" + str(BASE_DIR / "instance" / "connecthub.db")
    # Render/Heroku cũ có thể trả về "postgres://", SQLAlchemy yêu cầu "postgresql://"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _engine_options(url):
    opts = {"pool_pre_ping": True}  # kiểm tra kết nối trước khi dùng (Neon tạm dừng compute khi nhàn rỗi → kết nối cũ chết)
    if url.startswith("postgresql"):
        opts.update(
            pool_size=5, max_overflow=5,
            pool_recycle=240,  # < 5 phút: Neon Free tạm dừng sau 5 phút nhàn rỗi
            connect_args={"connect_timeout": 15},  # đủ thời gian cho Neon "đánh thức" compute
        )
    return opts


def _cors_origins():
    raw = os.environ.get("CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    # Render tự cung cấp RENDER_EXTERNAL_URL cho Web Service
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    return [render_url] if render_url else None  # None = chỉ cho phép cùng origin


class Config:
    ENV_NAME = os.environ.get("APP_ENV", "development")
    IS_PRODUCTION = ENV_NAME == "production"

    SECRET_KEY = os.environ.get("SECRET_KEY") or (None if IS_PRODUCTION else secrets.token_hex(32))

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options(SQLALCHEMY_DATABASE_URI)

    # Cookie phiên
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", IS_PRODUCTION)
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    WTF_CSRF_TIME_LIMIT = None  # token CSRF sống cùng phiên

    # Socket.IO
    CORS_ORIGINS = _cors_origins()

    # Giới hạn nghiệp vụ
    MAX_MESSAGE_LENGTH = int(os.environ.get("MAX_MESSAGE_LENGTH", 2000))
    MSG_RATE_LIMIT = (int(os.environ.get("MSG_RATE_COUNT", 10)), float(os.environ.get("MSG_RATE_WINDOW", 10)))
    # Giới hạn upload theo tài khoản: mặc định tối đa 5 file / 60 giây
    UPLOAD_RATE_LIMIT = (int(os.environ.get("UPLOAD_RATE_COUNT", 5)), float(os.environ.get("UPLOAD_RATE_WINDOW", 60)))
    REGISTER_RATE_LIMIT = int(os.environ.get("REGISTER_RATE_COUNT", 20))  # số lần đăng ký / giờ / IP
    MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", 10))
    MAX_CONTENT_LENGTH = (MAX_UPLOAD_MB + 1) * 1024 * 1024
    HISTORY_PAGE_SIZE = 30
    TRUST_PROXY = _env_bool("TRUST_PROXY", IS_PRODUCTION)

    # Lưu trữ file: "local" (thư mục / Render Persistent Disk) hoặc "s3" (S3-compatible: R2, B2, S3...)
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", str(BASE_DIR / "uploads"))
    S3_BUCKET = os.environ.get("S3_BUCKET")
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
    S3_REGION = os.environ.get("S3_REGION", "auto")
    S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID")
    S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY")
    S3_PREFIX = os.environ.get("S3_PREFIX", "connecthub/")


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    CORS_ORIGINS = None
    MAX_UPLOAD_MB = 1
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    MSG_RATE_LIMIT = (5, 10)
    UPLOAD_RATE_LIMIT = (1000, 60)  # các test cũ upload nhiều file; test giới hạn tự đặt lại giá trị
    REGISTER_RATE_LIMIT = 1000
