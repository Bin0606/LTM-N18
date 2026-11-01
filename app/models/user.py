from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..utils import iso, utcnow


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), nullable=False, unique=True, index=True)  # luôn lưu chữ thường
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)    # luôn lưu chữ thường
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(80), nullable=False)
    avatar = db.Column(db.String(255), nullable=True)  # storage_key của ảnh đại diện
    status_text = db.Column(db.String(120), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)  # scrypt/pbkdf2 + salt, không lưu mật khẩu thuần

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def avatar_url(self):
        if not self.avatar:
            return None
        version = self.avatar.rsplit("-", 1)[-1].split(".")[0][:8]  # đổi ảnh → URL đổi → bỏ cache cũ
        return f"/users/{self.id}/avatar?v={version}"

    def to_dict(self, online=None):
        data = {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "status_text": self.status_text or "",
            "avatar_url": self.avatar_url,
            "created_at": iso(self.created_at),
        }
        if online is not None:
            data["online"] = online
        return data
