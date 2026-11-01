import re

from flask import current_app
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import User
from .errors import ServiceError
from .ratelimit import SlidingWindowLimiter

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_registration(full_name, username, email, password, confirm):
    errors = {}
    if not (2 <= len(full_name) <= 80):
        errors["full_name"] = "Họ tên phải từ 2 đến 80 ký tự."
    if not USERNAME_RE.match(username):
        errors["username"] = "Username 3-32 ký tự, chỉ gồm chữ, số, dấu chấm và gạch dưới."
    if len(email) > 255 or not EMAIL_RE.match(email):
        errors["email"] = "Email không hợp lệ."
    if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        errors["password"] = "Mật khẩu tối thiểu 8 ký tự, gồm cả chữ và số."
    if password != confirm:
        errors["confirm"] = "Mật khẩu nhập lại không khớp."
    return errors


def register_user(full_name, username, email, password, confirm):
    full_name, username, email = full_name.strip(), username.strip().lower(), email.strip().lower()
    errors = validate_registration(full_name, username, email, password, confirm)
    if not errors:
        if User.query.filter_by(username=username).first():
            errors["username"] = "Username đã được sử dụng."
        if User.query.filter_by(email=email).first():
            errors["email"] = "Email đã được sử dụng."
    if errors:
        return None, errors
    user = User(full_name=full_name, username=username, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user, {}


# --- Chống dò mật khẩu / spam đăng nhập: 3 lớp giới hạn độc lập (cửa sổ 10 phút) ---
#   1. cùng IP + cùng tài khoản : 8 lần sai      (chặn dò mật khẩu một tài khoản từ một nơi)
#   2. cùng IP, mọi tài khoản   : 40 lần sai     (chặn quét nhiều username từ một nơi)
#   3. cùng tài khoản, mọi IP   : 30 lần sai     (chặn tấn công phân tán; ngưỡng cao để kẻ xấu khó khóa nạn nhân)
# Thông báo khi bị chặn LUÔN GIỐNG NHAU dù tài khoản có tồn tại hay không (không lộ thông tin).
FAIL_WINDOW = 600
LIMIT_IP_ID, LIMIT_IP, LIMIT_ID = 8, 40, 30
REGISTER_WINDOW = 3600
_limiter = SlidingWindowLimiter()
_DUMMY_HASH = None


def _dummy_hash():
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = generate_password_hash("connecthub-dummy-password")
    return _DUMMY_HASH


def _keys(identifier, ip):
    return f"lf:ipid:{ip}|{identifier}", f"lf:ip:{ip}", f"lf:id:{identifier}"


def _login_block_wait(identifier, ip):
    k1, k2, k3 = _keys(identifier, ip)
    waits = []
    for key, limit in ((k1, LIMIT_IP_ID), (k2, LIMIT_IP), (k3, LIMIT_ID)):
        blocked, wait = _limiter.blocked(key, limit, FAIL_WINDOW)
        if blocked:
            waits.append(wait)
    return max(waits) if waits else 0


def authenticate(identifier, password, ip="-"):
    if not isinstance(identifier, str) or not isinstance(password, str):
        return None
    identifier = identifier.strip().lower()[:254]  # cắt độ dài để không bị lạm dụng làm khóa bộ nhớ
    wait = _login_block_wait(identifier, ip)
    if wait:
        raise ServiceError("Quá nhiều lần đăng nhập sai. Vui lòng thử lại sau ít phút.", 429, retry_after=wait)
    user = User.query.filter(or_(User.username == identifier, User.email == identifier)).first()
    if user:
        ok = user.check_password(password)
    else:
        check_password_hash(_dummy_hash(), password)  # tốn thời gian tương đương → không lộ tài khoản có tồn tại
        ok = False
    if not ok:
        for key in _keys(identifier, ip):
            _limiter.add(key, FAIL_WINDOW)
        return None
    _limiter.clear(_keys(identifier, ip)[0])
    return user


def check_register_rate(ip):
    """Giới hạn số lần đăng ký từ một IP (mặc định 20/giờ, cấu hình REGISTER_RATE_COUNT)."""
    limit = current_app.config.get("REGISTER_RATE_LIMIT", 20)
    allowed, wait = _limiter.hit(f"reg:{ip}", limit, REGISTER_WINDOW)
    if not allowed:
        raise ServiceError("Bạn tạo tài khoản quá nhiều lần từ mạng này. Vui lòng thử lại sau.", 429, retry_after=wait)


def reset_login_limits():
    _limiter.reset()
