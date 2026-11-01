"""Kiểm thử S3Storage bằng S3 giả lập (moto, chạy trong tiến trình). Đây KHÔNG phải kiểm thử với Cloudflare R2 thật."""
import io

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from moto import mock_aws

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.services.storage import S3Storage, StorageError, StorageNotFound, StorageUnavailable
from conftest import login, register, tiny_png
from test_files import up
from test_rooms import mk_room

BUCKET = "connecthub-test"


def make_storage(bucket=BUCKET, **kw):
    return S3Storage(bucket=bucket, prefix="connecthub/", region="us-east-1", access_key="x", secret_key="y", **kw)


@mock_aws
def test_s3_save_open_delete_roundtrip():
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
    st = make_storage()
    st.save("files/1/a.png", io.BytesIO(b"hello"), "image/png")
    h = st.open("files/1/a.png")
    assert h.read() == b"hello"
    st.delete("files/1/a.png")
    with pytest.raises(StorageNotFound):
        st.open("files/1/a.png")


@mock_aws
def test_r2_compatible_checksum_config_and_path_style():
    st = make_storage()
    cfg = st.client.meta.config
    assert cfg.request_checksum_calculation == "when_required" and cfg.response_checksum_validation == "when_required"
    assert cfg.s3["addressing_style"] == "path" and cfg.signature_version == "s3v4"


@mock_aws
def test_s3_error_classification(monkeypatch):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
    st = make_storage()
    with pytest.raises(StorageNotFound):                       # key không có → 404
        st.open("missing")
    with pytest.raises(StorageUnavailable):                    # bucket sai/không tồn tại → lỗi cấu hình/storage, KHÔNG phải 404
        make_storage(bucket="no-such-bucket-xyz").open("k")

    def boom(exc):
        def f(**kw): raise exc
        return f
    for exc in (EndpointConnectionError(endpoint_url="http://x"),                                    # mất mạng
                ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}, "ResponseMetadata": {"HTTPStatusCode": 403}}, "GetObject"),
                ClientError({"Error": {"Code": "SlowDown", "Message": "x"}, "ResponseMetadata": {"HTTPStatusCode": 503}}, "GetObject"),
                ClientError({"Error": {"Code": "InternalError", "Message": "x"}, "ResponseMetadata": {"HTTPStatusCode": 500}}, "GetObject")):
        monkeypatch.setattr(st.client, "get_object", boom(exc))
        with pytest.raises(StorageUnavailable):
            st.open("k")
    monkeypatch.setattr(st.client, "upload_fileobj", lambda *a, **k: (_ for _ in ()).throw(EndpointConnectionError(endpoint_url="x")))
    with pytest.raises(StorageUnavailable):
        st.save("k", io.BytesIO(b"x"))


def test_s3_requires_credentials_and_bucket():
    with pytest.raises(StorageError) as e:
        S3Storage(bucket=None, access_key=None, secret_key=None)
    assert "S3_BUCKET" in str(e.value) and "S3_ACCESS_KEY_ID" in str(e.value)


def test_local_storage_errors(tmp_path):
    from app.services.storage import LocalStorage
    st = LocalStorage(tmp_path)
    with pytest.raises(StorageNotFound):
        st.open("nope/x.png")
    with pytest.raises(StorageError):
        st.open("../../etc/passwd")                                              # path traversal bị chặn
    st.save("a/b.txt", io.BytesIO(b"x"))
    assert st.open("a/b.txt").read() == b"x"
    st.delete("a/b.txt"); st.delete("a/b.txt")                                   # xóa lần 2 không lỗi


class S3Cfg(TestConfig):
    STORAGE_BACKEND = "s3"
    S3_BUCKET = BUCKET
    S3_REGION = "us-east-1"
    S3_ACCESS_KEY_ID = "x"
    S3_SECRET_ACCESS_KEY = "y"
    S3_PREFIX = "connecthub/"
    S3_ENDPOINT_URL = None


@mock_aws
def test_app_end_to_end_with_s3_backend(monkeypatch):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
    app = create_app(S3Cfg)
    with app.app_context():
        db.create_all()
    a, eve = app.test_client(), app.test_client()
    register(a, "alice"); login(a, "alice"); register(eve, "eve"); login(eve, "eve")
    room = mk_room(a)
    r = up(a, room["id"], "anh.png", tiny_png(), "image/png")
    assert r.status_code == 201
    f = r.get_json()["message"]["file"]
    keys = [o["Key"] for o in boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert len(keys) == 1 and keys[0].startswith(f"connecthub/files/{room['id']}/") and keys[0].endswith(".png")
    d = a.get(f["url"])
    assert d.status_code == 200 and d.data == tiny_png() and d.headers["Content-Disposition"].startswith("inline")
    assert eve.get(f["url"]).status_code == 404
    # R2 hỏng/mất kết nối: người DÙNG CÓ QUYỀN nhận 503 (không phải 404/500)
    st = app.extensions["storage"]
    monkeypatch.setattr(st.client, "get_object", lambda **kw: (_ for _ in ()).throw(EndpointConnectionError(endpoint_url="x")))
    assert a.get(f["url"]).status_code == 503
    monkeypatch.undo()
    # file bị xóa khỏi bucket → 404
    boto3.client("s3", region_name="us-east-1").delete_object(Bucket=BUCKET, Key=keys[0])
    assert a.get(f["url"]).status_code == 404
    # xóa phòng dọn file trên bucket
    r = up(a, room["id"], "b.png", tiny_png()); assert r.status_code == 201
    assert a.delete(f"/api/rooms/{room['id']}").status_code == 200
    assert boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket=BUCKET).get("KeyCount", 0) == 0


def test_app_refuses_to_start_with_incomplete_s3_config():
    class Bad(S3Cfg):
        S3_ACCESS_KEY_ID = None
    with pytest.raises(StorageError):
        create_app(Bad)
