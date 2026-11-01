from ..extensions import db
from ..utils import utcnow


class Message(db.Model):
    __tablename__ = "messages"
    __table_args__ = (
        db.Index("ix_messages_room_id_id", "room_id", "id"),  # phân trang lịch sử theo phòng
        db.UniqueConstraint("sender_id", "client_msg_id", name="uq_sender_client_msg"),  # chống gửi trùng khi reconnect
    )

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(10), nullable=False, default="text")  # text | file | system
    client_msg_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    sender = db.relationship("User")
    shared_file = db.relationship("SharedFile", backref="message", uselist=False, cascade="all, delete-orphan",
                                  passive_deletes=True)
