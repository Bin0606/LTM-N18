"""Lớp trừu tượng lưu trữ file.

- LocalStorage: lưu vào thư mục trên ổ đĩa (dev). Trên Render Free ổ đĩa là TẠM THỜI → không dùng cho production miễn phí.
- S3Storage: object storage tương thích S3 (Cloudflare R2, Backblaze B2, AWS S3...). Đây là cách giữ file bền vững
  khi chạy Render Free.

Database chỉ lưu `storage_key`, không lưu nội dung file.

Phân loại lỗi (để tầng trên trả mã HTTP đúng):
  StorageNotFound    → file không có trên storage      → 404
  StorageUnavailable → lỗi kết nối/xác thực/quá tải     → 503 (KHÔNG biến thành 404)
"""
from pathlib import Path


class StorageError(Exception):
    pass


class StorageNotFound(StorageError):
    pass


class StorageUnavailable(StorageError):
    pass


class LocalStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        path = (self.root / key).resolve()
        if self.root not in path.parents:  # chặn path traversal
            raise StorageError("Khóa lưu trữ không hợp lệ")
        return path

    def save(self, key, fileobj, content_type=None):
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as out:
                while True:
                    chunk = fileobj.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
        except OSError as exc:
            raise StorageUnavailable("Không ghi được vào thư mục lưu trữ") from exc

    def open(self, key):
        path = self._path(key)
        try:
            return open(path, "rb")
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
            raise StorageNotFound("File không tồn tại trên storage") from exc
        except OSError as exc:  # quyền truy cập, ổ đĩa lỗi...
            raise StorageUnavailable("Không đọc được thư mục lưu trữ") from exc

    def delete(self, key):
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise StorageUnavailable("Không xóa được file trên storage") from exc


class S3Storage:
    NOT_FOUND_CODES = {"NoSuchKey", "404", "NotFound"}

    def __init__(self, bucket, prefix="", endpoint_url=None, region="auto", access_key=None, secret_key=None):
        import boto3  # import muộn để môi trường dev không bắt buộc cài/cấu hình S3
        from botocore.config import Config
        missing = [n for n, v in (("S3_BUCKET", bucket), ("S3_ACCESS_KEY_ID", access_key),
                                  ("S3_SECRET_ACCESS_KEY", secret_key)) if not v]
        if missing:
            raise StorageError("Thiếu cấu hình storage S3/R2: " + ", ".join(missing))
        self.bucket = bucket
        self.prefix = prefix or ""
        cfg = Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
            s3={"addressing_style": "path"},
            # Cloudflare R2: boto3 >= 1.36 mặc định thêm checksum CRC32 mà R2 có thể từ chối → chỉ tính khi bắt buộc.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        self.client = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region,
                                   aws_access_key_id=access_key, aws_secret_access_key=secret_key, config=cfg)

    def _k(self, key):
        return f"{self.prefix}{key}"

    def _classify(self, exc, action):
        from botocore.exceptions import ClientError
        if isinstance(exc, ClientError):
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if action == "open" and (code in self.NOT_FOUND_CODES or status == 404) and code != "NoSuchBucket":
                return StorageNotFound("File không tồn tại trên storage")
        # mọi lỗi khác (AccessDenied, NoSuchBucket, hết hạn mức, timeout, mất mạng, 5xx...) là lỗi kho lưu trữ
        return StorageUnavailable(f"Kho lưu trữ lỗi khi {action}")

    def save(self, key, fileobj, content_type=None):
        extra = {"ContentType": content_type} if content_type else {}
        try:
            self.client.upload_fileobj(fileobj, self.bucket, self._k(key), ExtraArgs=extra)
        except Exception as exc:
            raise self._classify(exc, "save") from exc

    def open(self, key):
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"]
        except Exception as exc:
            raise self._classify(exc, "open") from exc

    def delete(self, key):
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._k(key))
        except Exception as exc:
            raise self._classify(exc, "delete") from exc


def build_storage(config):
    backend = config.get("STORAGE_BACKEND", "local")
    if backend == "s3":
        return S3Storage(
            bucket=config.get("S3_BUCKET"),
            prefix=config.get("S3_PREFIX", ""),
            endpoint_url=config.get("S3_ENDPOINT_URL"),
            region=config.get("S3_REGION", "auto"),
            access_key=config.get("S3_ACCESS_KEY_ID"),
            secret_key=config.get("S3_SECRET_ACCESS_KEY"),
        )
    if backend == "local":
        return LocalStorage(config["UPLOAD_DIR"])
    raise StorageError(f"STORAGE_BACKEND không hợp lệ: {backend}")
