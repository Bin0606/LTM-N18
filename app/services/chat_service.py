import threading
import time
from collections import defaultdict, deque

from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Message, RoomMember
from ..utils import iso
from .errors import ServiceError


def serialize_message(msg):
    data = {
        "id": msg.id,
        "room_id": msg.room_id,
        "type": msg.message_type,
        "content": msg.content,
        "created_at": iso(msg.created_at),
        "client_msg_id": msg.client_msg_id,
        "sender": {
            "id": msg.sender.id,
            "username": msg.sender.username,
            "full_name": msg.sender.full_name,
            "avatar_url": msg.sender.avatar_url,
        },
        "file": None,
    }
    if msg.message_type == "file" and msg.shared_file:
        data["file"] = msg.shared_file.to_dict()
    return data


# --- Giới hạn tần suất gửi tin nhắn: tối đa N tin trong T giây cho mỗi user ---
_rl_lock = threading.Lock()
_rl = defaultdict(deque)


def check_rate_limit(user_id):
    limit, window = current_app.config["MSG_RATE_LIMIT"]
    now = time.monotonic()
    with _rl_lock:
        q = _rl[user_id]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise ServiceError("Bạn gửi tin quá nhanh. Vui lòng chậm lại một chút.", 429)
        q.append(now)


def reset_rate_limits():
    with _rl_lock:
        _rl.clear()


def is_member(user_id, room_id):
    return db.session.query(RoomMember.id).filter_by(room_id=room_id, user_id=user_id).first() is not None


def clean_content(raw):
    """Chuẩn hóa nội dung. KHÔNG escape HTML ở server; client luôn render bằng textContent."""
    if not isinstance(raw, str):
        raise ServiceError("Nội dung tin nhắn không hợp lệ.")
    content = raw.replace("\r\n", "\n").strip()
    if not content:
        raise ServiceError("Tin nhắn không được để trống.")
    if len(content) > current_app.config["MAX_MESSAGE_LENGTH"]:
        raise ServiceError(f"Tin nhắn tối đa {current_app.config['MAX_MESSAGE_LENGTH']} ký tự.")
    return content


def create_text_message(user, room_id, raw_content, client_msg_id=None):
    """Kiểm tra quyền + nội dung, LƯU DB, rồi mới trả về tin nhắn. Lỗi → rollback và ném ServiceError."""
    if not is_member(user.id, room_id):
        raise ServiceError("Bạn không thuộc phòng này.", 403)
    content = clean_content(raw_content)
    if client_msg_id is not None:
        client_msg_id = str(client_msg_id)[:64]
        existing = Message.query.filter_by(sender_id=user.id, client_msg_id=client_msg_id).first()
        if existing:  # client gửi lại sau khi reconnect → trả tin cũ, không tạo trùng
            return existing, False
    check_rate_limit(user.id)
    msg = Message(room_id=room_id, sender_id=user.id, content=content, message_type="text",
                  client_msg_id=client_msg_id)
    try:
        db.session.add(msg)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = Message.query.filter_by(sender_id=user.id, client_msg_id=client_msg_id).first()
        if existing:
            return existing, False
        raise ServiceError("Không thể lưu tin nhắn.", 500)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Lỗi lưu tin nhắn")
        raise ServiceError("Máy chủ không lưu được tin nhắn. Vui lòng thử lại.", 500)
    return msg, True


def create_system_message(user, room_id, text):
    msg = Message(room_id=room_id, sender_id=user.id, content=text, message_type="system")
    try:
        db.session.add(msg)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Lỗi lưu tin nhắn hệ thống")
        return None
    return msg


def list_messages(room_id, before_id=None, after_id=None, limit=30, q=None):
    query = Message.query.filter_by(room_id=room_id)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.filter(Message.message_type != "system", Message.content.ilike(like, escape="\\"))
    if after_id:
        rows = query.filter(Message.id > after_id).order_by(Message.id.asc()).limit(200).all()
        return rows, False
    if before_id:
        query = query.filter(Message.id < before_id)
    rows = query.order_by(Message.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = list(reversed(rows[:limit]))
    return rows, has_more
