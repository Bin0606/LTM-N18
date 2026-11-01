from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def index():
    return render_template("chat.html")


@bp.route("/join/<code>")
def join_link(code):
    """Link mời: /join/<mã>. Chưa đăng nhập → chuyển sang đăng nhập rồi quay lại. Việc tham gia thật
    được thực hiện bằng POST có CSRF từ JavaScript (GET không được thay đổi dữ liệu)."""
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=url_for("main.join_link", code=code)))
    return render_template("chat.html", join_code=code)


@bp.route("/healthz")
def healthz():
    return "ok", 200
