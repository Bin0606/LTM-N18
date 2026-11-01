import io
import os

from conftest import tiny_docx, tiny_png
from test_rooms import mk_room


def up(c, room_id, name, data, mime=None):
    return c.post(f"/api/rooms/{room_id}/files", data={"file": (io.BytesIO(data), name, mime or "")},
                  content_type="multipart/form-data")


def test_upload_download_permissions(make_user, app):
    a, b, outsider = make_user("alice"), make_user("bob"), make_user("eve")
    room = mk_room(a)
    b.post("/api/rooms/join", json={"code": room["invite_code"]})
    r = up(a, room["id"], "../../etc/ảnh đẹp.png", tiny_png(), "image/png")
    assert r.status_code == 201
    f = r.get_json()["message"]["file"]
    assert "/" not in f["name"] and f["is_image"]
    stored = os.listdir(os.path.join(app.config["UPLOAD_DIR"], "files", str(room["id"])))
    assert len(stored) == 1 and stored[0].endswith(".png") and "ảnh" not in stored[0]  # tên lưu ngẫu nhiên
    d = b.get(f["url"])
    assert d.status_code == 200 and d.data == tiny_png() and d.headers["X-Content-Type-Options"] == "nosniff"
    assert outsider.get(f["url"]).status_code == 404                   # không thuộc phòng → không tải được
    assert up(outsider, room["id"], "a.png", tiny_png()).status_code == 403
    assert len(b.get(f"/api/rooms/{room['id']}/files").get_json()["files"]) == 1
    assert len(b.get(f"/api/rooms/{room['id']}/files?q=%25").get_json()["files"]) == 0  # ký tự % được escape
    msgs = b.get(f"/api/rooms/{room['id']}/messages").get_json()["messages"]
    assert msgs[-1]["type"] == "file" and msgs[-1]["file"]["id"] == f["id"]


def test_docx_pdf_txt_ok_and_pdf_attachment(make_user):
    a = make_user("alice"); room = mk_room(a)
    assert up(a, room["id"], "bao cao.docx", tiny_docx()).status_code == 201
    assert up(a, room["id"], "a.pdf", b"%PDF-1.4 test").status_code == 201
    assert up(a, room["id"], "ghi chu.txt", "Xin chào Việt Nam".encode("utf-8")).status_code == 201
    fid = a.get(f"/api/rooms/{room['id']}/files").get_json()["files"][1]["id"]
    assert a.get(f"/files/{fid}").headers["Content-Disposition"].startswith("attachment")


def test_rejects_bad_uploads(make_user):
    a = make_user("alice"); room = mk_room(a)
    assert up(a, room["id"], "virus.exe", b"MZ\x90").status_code == 400
    assert up(a, room["id"], "page.html", b"<script>").status_code == 400
    assert up(a, room["id"], "fake.png", b"<html>not a png</html>").status_code == 400   # sai nội dung
    assert up(a, room["id"], "fake.pdf", b"%PDF-1", "text/html").status_code == 400      # MIME sai
    assert up(a, room["id"], "x.docx", b"not a zip").status_code == 400
    assert up(a, room["id"], "bin.txt", b"abc\x00def").status_code == 400
    assert up(a, room["id"], "empty.txt", b"").status_code == 400
    r = up(a, room["id"], "big.png", tiny_png() + b"0" * (1024 * 1024 + 10))
    assert r.status_code == 413 and "MB" in r.get_json()["error"]
    assert up(a, room["id"], "huge.txt", b"a" * (3 * 1024 * 1024)).status_code == 413    # vượt MAX_CONTENT_LENGTH
    assert a.get("/files/9999").status_code == 404
