"""Khởi tạo các extension ở một nơi để tránh import vòng."""
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
# async_mode="threading": dùng simple-websocket + gunicorn gthread, không cần monkey-patch
socketio = SocketIO(async_mode="threading", manage_session=False)
