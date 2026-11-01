import secrets
from urllib.parse import unquote, urlparse

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..services import auth_service
from ..services.errors import ServiceError
from ..sockets import presence

bp = Blueprint("auth", __name__)


def _safe_next(target):
    """Chỉ chấp nhận đường dẫn nội bộ dạng "/abc". Chống open redirect, gồm các biến thể trình duyệt tự "sửa":
    "//host", "/\\host" (dấu \\ bị coi như /), tab/xuống dòng chèn giữa ("/\t/host"), mã hóa %5C %2F %09, URL tuyệt đối,
    "javascript:". Kiểm tra cả bản gốc lẫn bản đã giải mã % (tối đa 2 lớp). Không hợp lệ → về trang chủ."""
    default = url_for("main.index")
    if not isinstance(target, str) or not target or len(target) > 512 or not target.isascii():
        return default
    candidate = target
    for _ in range(3):  # bản gốc, giải mã 1 lớp, giải mã 2 lớp
        if any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in candidate):  # điều khiển, khoảng trắng, backslash
            return default
        if not candidate.startswith("/") or candidate.startswith("//"):
            return default
        parsed = urlparse(candidate)
        if parsed.scheme or parsed.netloc:
            return default
        candidate = unquote(candidate)
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        identifier = request.form.get("identifier", "")
        password = request.form.get("password", "")
        try:
            user = auth_service.authenticate(identifier, password, request.remote_addr or "-")
        except ServiceError as exc:
            flash(exc.message, "error")
            return render_template("login.html", identifier=identifier), exc.status
        if not user:
            flash("Email/username hoặc mật khẩu không đúng.", "error")
            return render_template("login.html", identifier=identifier), 401
        session.clear()
        session["sk"] = secrets.token_hex(8)  # khóa phiên: dùng để ngắt đúng các WebSocket của trình duyệt này
        session.permanent = True
        login_user(user)
        return redirect(_safe_next(request.args.get("next")))
    return render_template("login.html", identifier="")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        try:
            auth_service.check_register_rate(request.remote_addr or "-")
        except ServiceError as exc:
            flash(exc.message, "error")
            return render_template("register.html", errors={}, form=request.form), exc.status
        f = request.form
        user, errors = auth_service.register_user(f.get("full_name", ""), f.get("username", ""), f.get("email", ""),
                                                  f.get("password", ""), f.get("confirm", ""))
        if errors:
            return render_template("register.html", errors=errors, form=f), 400
        flash("Tạo tài khoản thành công. Hãy đăng nhập.", "success")
        return redirect(url_for("auth.login"))
    return render_template("register.html", errors={}, form={})


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    from ..extensions import socketio
    sk = session.get("sk")
    if sk:  # ngắt các kết nối WebSocket thuộc phiên đăng nhập này
        for sid in presence.sids_for_session(sk):
            socketio.server.disconnect(sid)
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))
