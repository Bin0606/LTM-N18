import uuid

from flask import Blueprint, Response, abort, current_app, jsonify, request
from flask_login import current_user, login_required

from ..extensions import db, socketio
from ..models import User
from ..services import file_service, room_service
from ..services.errors import ServiceError
from ..services.storage import StorageUnavailable
from ..services.validation import as_text, json_dict
from .file import open_storage

bp = Blueprint("users", __name__)


def _me():
    return {**current_user.to_dict(), "email": current_user.email}


@bp.get("/api/me")
@login_required
def me():
    return jsonify({"user": _me()})


@bp.patch("/api/me")
@login_required
def update_me():
    data = json_dict()
    if "full_name" not in data and "status_text" not in data:
        raise ServiceError("Không có thông tin nào để cập nhật.")
    if "full_name" in data:
        current_user.full_name = as_text(data["full_name"], "Tên hiển thị", min_len=2, max_len=80)
    if "status_text" in data:
        current_user.status_text = as_text(data["status_text"], "Trạng thái", max_len=120, required=False)
    db.session.commit()
    _broadcast_profile()
    return jsonify({"user": _me()})


@bp.post("/api/me/avatar")
@login_required
def upload_avatar():
    file_service.check_upload_rate_limit(current_user.id)
    f = request.files.get("file")
    ext, mime = file_service.validate_avatar(f)
    storage = current_app.extensions["storage"]
    key = f"avatars/{current_user.id}-{uuid.uuid4().hex}.{ext}"
    try:
        storage.save(key, f.stream, mime)
    except StorageUnavailable:
        current_app.logger.exception("Kho lưu trữ không khả dụng khi lưu avatar")
        raise ServiceError("Kho lưu trữ tạm thời không khả dụng. Vui lòng thử lại sau.", 503, retry_after=30)
    except Exception:
        current_app.logger.exception("Lỗi lưu avatar")
        raise ServiceError("Không lưu được ảnh đại diện. Vui lòng thử lại.", 500)
    old = current_user.avatar
    current_user.avatar = key
    db.session.commit()
    if old:
        file_service.delete_keys([old])
    _broadcast_profile()
    return jsonify({"user": _me()})


@bp.get("/users/<int:user_id>/avatar")
@login_required
def avatar(user_id):
    user = db.session.get(User, user_id)
    if not user or not user.avatar:
        abort(404)
    ext = user.avatar.rsplit(".", 1)[-1]
    mime = file_service.ALLOWED.get(ext, ("application/octet-stream",))[0]
    handle = open_storage(user.avatar)  # 404 nếu file mất, 503 nếu storage lỗi (không gộp chung)
    try:
        body = handle.read()  # ảnh đại diện ≤ 2 MB
    finally:
        handle.close()
    return Response(body, mimetype=mime, headers={"X-Content-Type-Options": "nosniff",
                                                  "Cache-Control": "private, max-age=3600"})


def _broadcast_profile():
    for rid in room_service.my_room_ids(current_user.id):
        socketio.emit("members:changed", {"room_id": rid}, to=f"room:{rid}")
