"""Hàm phát sự kiện Socket.IO dùng chung cho cả HTTP route và socket handler."""
from ..extensions import socketio


def notify_rooms_changed(user_ids):
    """Báo cho mọi thiết bị của các user tải lại danh sách phòng."""
    for uid in set(user_ids):
        socketio.emit("rooms:changed", {}, to=f"user:{uid}")
