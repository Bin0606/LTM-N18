import secrets

from flask import current_app
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Message, Room, RoomMember, SharedFile, User
from ..sockets import presence
from .errors import ServiceError
from .validation import as_int, as_text


def new_invite_code():
    while True:
        code = secrets.token_urlsafe(9)  # 72 bit ngẫu nhiên, không dùng ID tuần tự
        if not Room.query.filter_by(invite_code=code).first():
            return code


def get_membership(user_id, room_id):
    return RoomMember.query.filter_by(user_id=user_id, room_id=room_id).first()


def require_member(user, room_id):
    member = get_membership(user.id, room_id)
    if not member:
        raise ServiceError("Bạn không có quyền truy cập phòng này.", 403)
    return member


def require_admin(user, room_id):
    member = require_member(user, room_id)
    if member.role != "admin":
        raise ServiceError("Chỉ quản trị viên phòng mới thực hiện được thao tác này.", 403)
    return member


def create_room(user, name, description):
    name = as_text(name, "Tên phòng", min_len=2, max_len=80)
    description = as_text(description, "Mô tả", max_len=500, required=False, multiline=True)
    room = Room(room_name=name, description=description, owner_id=user.id, invite_code=new_invite_code())
    db.session.add(room)
    db.session.flush()
    db.session.add(RoomMember(room_id=room.id, user_id=user.id, role="admin"))
    db.session.commit()
    return room


def update_room(user, room_id, name=None, description=None):
    require_admin(user, room_id)
    room = db.session.get(Room, room_id)
    if room.is_direct:
        raise ServiceError("Không thể sửa cuộc trò chuyện cá nhân.")
    if name is None and description is None:
        raise ServiceError("Không có thông tin nào để cập nhật.")
    if name is not None:
        room.room_name = as_text(name, "Tên phòng", min_len=2, max_len=80)
    if description is not None:
        room.description = as_text(description, "Mô tả", max_len=500, required=False, multiline=True)
    db.session.commit()
    return room


def join_by_code(user, code):
    code = as_text(code, "Mã mời", max_len=64)
    room = Room.query.filter_by(invite_code=code).first()
    if not room or room.is_direct or not room.is_active:
        raise ServiceError("Mã mời không hợp lệ hoặc phòng không tồn tại.", 404)
    if get_membership(user.id, room.id):
        return room, False
    try:
        db.session.add(RoomMember(room_id=room.id, user_id=user.id, role="member"))
        db.session.commit()
    except IntegrityError:
        # hai yêu cầu tham gia đồng thời (vd. bấm 2 lần / 2 thiết bị): ràng buộc UNIQUE(room_id, user_id) chặn bản ghi thứ hai
        db.session.rollback()
        return room, False
    return room, True


def leave_room(user, room_id):
    member = require_member(user, room_id)
    room = db.session.get(Room, room_id)
    if room.is_direct:
        raise ServiceError("Không thể rời cuộc trò chuyện cá nhân.")
    if room.owner_id == user.id:
        raise ServiceError("Chủ phòng không thể rời phòng. Hãy xóa phòng nếu không còn cần.")
    db.session.delete(member)
    db.session.commit()
    return room


def delete_room(user, room_id):
    require_admin(user, room_id)
    room = db.session.get(Room, room_id)
    if room.is_direct:
        raise ServiceError("Không thể xóa cuộc trò chuyện cá nhân.")
    if room.owner_id != user.id:
        raise ServiceError("Chỉ chủ phòng mới được xóa phòng.", 403)
    files = [f.storage_key for f in SharedFile.query.filter_by(room_id=room_id).all()]
    db.session.delete(room)
    db.session.commit()
    return files  # để route xóa file vật lý trên storage


def kick_member(user, room_id, target_id):
    target_id = as_int(target_id, "Thành viên")
    require_admin(user, room_id)
    room = db.session.get(Room, room_id)
    if target_id == room.owner_id:
        raise ServiceError("Không thể xóa chủ phòng.")
    target = get_membership(target_id, room_id)
    if not target:
        raise ServiceError("Thành viên không tồn tại trong phòng.", 404)
    db.session.delete(target)
    db.session.commit()
    return room


def _find_dm(key):
    return Room.query.filter_by(direct_key=key).first()


def get_or_create_dm(user, other_id):
    other_id = as_int(other_id, "Người dùng")
    if other_id == user.id:
        raise ServiceError("Không thể nhắn tin cho chính mình.")
    other = db.session.get(User, other_id)
    if not other:
        raise ServiceError("Người dùng không tồn tại.", 404)
    key = f"{min(user.id, other_id)}:{max(user.id, other_id)}"
    room = _find_dm(key)
    if room:
        return room, other, False
    try:
        room = Room(room_name="Chat cá nhân", owner_id=user.id, invite_code=new_invite_code(),
                    is_direct=True, direct_key=key)
        db.session.add(room)
        db.session.flush()
        db.session.add(RoomMember(room_id=room.id, user_id=user.id, role="member"))
        db.session.add(RoomMember(room_id=room.id, user_id=other_id, role="member"))
        db.session.commit()
        return room, other, True
    except IntegrityError:
        # hai người cùng mở chat 1-1 cùng lúc: UNIQUE(direct_key) làm một bên thất bại → rollback, dùng phòng đã có
        db.session.rollback()
        room = _find_dm(key)
        if room:
            return room, other, False
        current_app.logger.exception("Không tạo được cuộc trò chuyện riêng")
        raise ServiceError("Không tạo được cuộc trò chuyện. Vui lòng thử lại.", 500)


def mark_read(user, room_id, raw_message_id):
    """Đánh dấu đã đọc tới một tin nhắn. Chỉ chấp nhận ID tin nhắn có THẬT và thuộc ĐÚNG phòng này."""
    member = require_member(user, room_id)
    message_id = as_int(raw_message_id, "ID tin nhắn")
    msg = db.session.get(Message, message_id)
    # cùng một thông báo cho "không tồn tại" và "thuộc phòng khác" để không lộ sự tồn tại của tin ở phòng khác
    if not msg or msg.room_id != room_id:
        raise ServiceError("Tin nhắn không tồn tại trong phòng này.", 404)
    if message_id > member.last_read_message_id:  # chỉ tiến, không lùi
        member.last_read_message_id = message_id
        db.session.commit()
    return member.last_read_message_id


def unread_count(member):
    return (db.session.query(func.count(Message.id))
            .filter(Message.room_id == member.room_id, Message.id > member.last_read_message_id,
                    Message.sender_id != member.user_id, Message.message_type != "system")
            .scalar())


def serialize_room_for(user, room, member=None):
    member = member or get_membership(user.id, room.id)
    data = room.to_dict()
    data["role"] = member.role
    data["unread"] = unread_count(member)
    data["member_count"] = RoomMember.query.filter_by(room_id=room.id).count()
    data["invite_code"] = None if room.is_direct else room.invite_code
    if room.is_direct:
        other = (User.query.join(RoomMember, RoomMember.user_id == User.id)
                 .filter(RoomMember.room_id == room.id, User.id != user.id).first())
        if other:
            data["peer"] = other.to_dict(online=presence.is_online(other.id))
            data["room_name"] = other.full_name
    return data


def room_members(room):
    rows = (RoomMember.query.filter_by(room_id=room.id).join(User).order_by(RoomMember.role.asc(), User.full_name.asc()).all())
    online = presence.online_ids([m.user_id for m in rows])
    result = []
    for m in rows:
        info = m.user.to_dict(online=m.user_id in online)
        info["role"] = m.role
        info["joined_at"] = m.joined_at.isoformat(timespec="seconds") + "Z"
        result.append(info)
    return result


def my_room_ids(user_id):
    return [rid for (rid,) in db.session.query(RoomMember.room_id).filter_by(user_id=user_id).all()]
