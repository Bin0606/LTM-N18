"""Application factory của ConnectHub."""
import logging
import os

from flask import Flask, jsonify, redirect, request, url_for
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .extensions import csrf, db, login_manager, migrate, socketio
from .services.errors import ServiceError
from .services.storage import build_storage


@event.listens_for(Engine, "connect")
def _sqlite_foreign_keys(dbapi_connection, _record):
    # SQLite mặc định KHÔNG bật khóa ngoại; bật để ON DELETE CASCADE hoạt động giống PostgreSQL
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _wants_json():
    return request.path.startswith(("/api/", "/files/", "/users/"))


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("Thiếu SECRET_KEY. Hãy đặt biến môi trường SECRET_KEY khi chạy production.")

    logging.basicConfig(level=logging.INFO)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    # Import handler TRƯỚC init_app để Flask-SocketIO lưu lại và gắn vào mỗi Server mới được tạo
    from .sockets import chat  # noqa: F401
    socketio.init_app(app, cors_allowed_origins=app.config.get("CORS_ORIGINS"))
    app.extensions["storage"] = build_storage(app.config)  # thiếu cấu hình S3/R2 → báo lỗi rõ ràng ngay khi khởi động
    if app.config["IS_PRODUCTION"] and app.config["STORAGE_BACKEND"] == "local":
        app.logger.warning("STORAGE_BACKEND=local ở production: trên Render Free ổ đĩa là TẠM THỜI, file sẽ mất khi "
                           "restart/redeploy. Dùng STORAGE_BACKEND=s3 (Cloudflare R2) hoặc gắn Persistent Disk.")

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized():
        if _wants_json():
            return jsonify({"error": "Bạn cần đăng nhập."}), 401
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

    from .routes import auth, file, main, room, user
    for module in (auth, main, room, file, user):
        app.register_blueprint(module.bp)

    # --- Xử lý lỗi: không để lộ chi tiết nội bộ ---
    @app.errorhandler(ServiceError)
    def handle_service_error(exc):
        resp = jsonify({"error": exc.message, **({"retry_after": exc.retry_after} if exc.retry_after else {})})
        resp.status_code = exc.status
        if exc.retry_after:
            resp.headers["Retry-After"] = str(exc.retry_after)
        return resp

    @app.errorhandler(CSRFError)
    def handle_csrf(exc):
        return jsonify({"error": "Phiên làm việc không hợp lệ hoặc đã hết hạn. Hãy tải lại trang."}), 400

    @app.errorhandler(HTTPException)
    def handle_http(exc):
        if _wants_json():
            messages = {413: "File vượt quá dung lượng cho phép.", 404: "Không tìm thấy.",
                        405: "Phương thức không được hỗ trợ."}
            return jsonify({"error": messages.get(exc.code, exc.description)}), exc.code
        return exc

    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        app.logger.exception("Lỗi không mong đợi")
        db.session.rollback()
        return jsonify({"error": "Có lỗi xảy ra phía máy chủ. Vui lòng thử lại sau."}), 500

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
            "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        if app.config["IS_PRODUCTION"]:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    # Tạo bảng tự động khi dùng SQLite lần đầu (dev). Production dùng `flask db upgrade`.
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite") and os.environ.get("AUTO_CREATE_DB", "1") != "0":
        with app.app_context():
            db.create_all()

    # ProxyFix phải bọc NGOÀI CÙNG (sau socketio.init_app) để scheme/host thật từ proxy của Render
    # được dùng cả cho kiểm tra Origin của Socket.IO lẫn cho cookie Secure.
    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    return app
