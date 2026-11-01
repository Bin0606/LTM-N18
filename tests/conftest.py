import io
import zipfile

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db, socketio
from app.services import auth_service, chat_service, file_service
from app.sockets import presence


class Cfg(TestConfig):
    pass


@pytest.fixture()
def app(tmp_path):
    Cfg.UPLOAD_DIR = str(tmp_path / "uploads")
    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
    # Không giữ app context mở trong lúc test: nếu giữ, Flask dùng lại cùng `g` cho mọi request
    # và Flask-Login sẽ cache nhầm người dùng giữa các client khác nhau.
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()
    presence.reset()
    auth_service.reset_login_limits()
    chat_service.reset_rate_limits()
    file_service.reset_upload_limits()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, username, email=None, password="Passw0rd!", name=None):
    return client.post("/register", data={
        "full_name": name or username.title(), "username": username, "email": email or f"{username}@example.com",
        "password": password, "confirm": password})


def login(client, username, password="Passw0rd!"):
    return client.post("/login", data={"identifier": username, "password": password})


@pytest.fixture()
def make_user(app):
    def _make(username):
        c = app.test_client()
        assert register(c, username).status_code == 302
        assert login(c, username).status_code == 302
        return c
    return _make


def tiny_png():
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def tiny_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()
