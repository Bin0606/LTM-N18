from ..extensions import db
from ..utils import iso, utcnow


class Room(db.Model):
    __tablename__ = "rooms"

    id = db.Column(db.Integer, primary_key=True)
    room_name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(500), nullable=False, default="")
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    invite_code = db.Column(db.String(32), nullable=False, unique=True, index=True)  # mã ngẫu nhiên, không đoán được
    is_direct = db.Column(db.Boolean, nullable=False, default=False)  # True = chat cá nhân 1-1
    direct_key = db.Column(db.String(40), nullable=True, unique=True)  # "minId:maxId" tránh tạo trùng
    is_active = db.Column(db.Boolean, nullable=False, default=True)  # trạng thái phòng
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    members = db.relationship("RoomMember", backref="room", cascade="all, delete-orphan", passive_deletes=True)
    messages = db.relationship("Message", backref="room", cascade="all, delete-orphan", passive_deletes=True)
    files = db.relationship("SharedFile", backref="room", cascade="all, delete-orphan", passive_deletes=True)

    def to_dict(self):
        return {
            "id": self.id,
            "room_name": self.room_name,
            "description": self.description,
            "owner_id": self.owner_id,
            "is_direct": self.is_direct,
            "is_active": self.is_active,
            "created_at": iso(self.created_at),
        }


class RoomMember(db.Model):
    __tablename__ = "room_members"
    __table_args__ = (db.UniqueConstraint("room_id", "user_id", name="uq_room_user"),)

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = db.Column(db.String(10), nullable=False, default="member")  # "admin" | "member"
    last_read_message_id = db.Column(db.Integer, nullable=False, default=0)
    joined_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship("User")
