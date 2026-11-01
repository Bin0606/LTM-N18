from ..extensions import db
from ..utils import iso, utcnow


class SharedFile(db.Model):
    __tablename__ = "shared_files"
    __table_args__ = (db.Index("ix_shared_files_room_created", "room_id", "created_at"),)

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True)
    uploader_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    original_filename = db.Column(db.String(255), nullable=False)  # chỉ để hiển thị / đặt tên khi tải
    stored_filename = db.Column(db.String(80), nullable=False, unique=True)  # tên ngẫu nhiên trên storage
    storage_key = db.Column(db.String(255), nullable=False)  # khóa trên storage, KHÔNG phải đường dẫn server
    file_size = db.Column(db.Integer, nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    uploader = db.relationship("User")

    @property
    def is_image(self):
        return self.mime_type.startswith("image/")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.original_filename,
            "size": self.file_size,
            "mime_type": self.mime_type,
            "is_image": self.is_image,
            "url": f"/files/{self.id}",
            "uploader": self.uploader.full_name if self.uploader else None,
            "created_at": iso(self.created_at),
        }
