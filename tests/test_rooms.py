def mk_room(c, name="Phòng 1"):
    r = c.post("/api/rooms", json={"name": name, "description": "mô tả"})
    assert r.status_code == 201
    return r.get_json()["room"]


def test_create_join_leave_delete(make_user):
    a, b = make_user("alice"), make_user("bob")
    room = mk_room(a)
    assert room["role"] == "admin" and len(room["invite_code"]) >= 12
    assert b.get(f"/api/rooms/{room['id']}").status_code == 403           # chưa là thành viên
    assert b.get(f"/api/rooms/{room['id']}/messages").status_code == 403
    r = b.post("/api/rooms/join", json={"code": room["invite_code"]})
    assert r.status_code == 200 and r.get_json()["joined"] is True
    assert b.post("/api/rooms/join", json={"code": room["invite_code"]}).get_json()["joined"] is False
    assert b.post("/api/rooms/join", json={"code": "sai-ma"}).status_code == 404
    assert len(a.get(f"/api/rooms/{room['id']}").get_json()["room"]["members"]) == 2
    assert b.patch(f"/api/rooms/{room['id']}", json={"name": "Hack"}).status_code == 403  # member không sửa được
    assert a.patch(f"/api/rooms/{room['id']}", json={"name": "Tên mới"}).status_code == 200
    assert b.delete(f"/api/rooms/{room['id']}").status_code == 403
    assert b.post(f"/api/rooms/{room['id']}/leave").status_code == 200
    assert b.get(f"/api/rooms/{room['id']}").status_code == 403
    assert a.post(f"/api/rooms/{room['id']}/leave").status_code == 400    # chủ phòng không rời được
    assert a.delete(f"/api/rooms/{room['id']}").status_code == 200
    assert a.get(f"/api/rooms/{room['id']}").status_code == 403


def test_kick_and_direct_message(make_user, app):
    from app.models import User
    a, b = make_user("alice"), make_user("bob")
    room = mk_room(a)
    b.post("/api/rooms/join", json={"code": room["invite_code"]})
    with app.app_context():
        bob_id = User.query.filter_by(username="bob").one().id
    assert b.delete(f"/api/rooms/{room['id']}/members/{bob_id}").status_code == 403
    assert a.delete(f"/api/rooms/{room['id']}/members/{bob_id}").status_code == 200
    assert b.get(f"/api/rooms/{room['id']}").status_code == 403
    dm = a.post("/api/dm", json={"user_id": bob_id}).get_json()["room"]
    assert dm["is_direct"] and dm["room_name"] == "Bob"
    assert a.post("/api/dm", json={"user_id": bob_id}).get_json()["room"]["id"] == dm["id"]  # không tạo trùng
    assert b.get(f"/api/rooms/{dm['id']}").status_code == 200
    assert b.post("/api/rooms/join", json={"code": dm["invite_code"] or "x"}).status_code == 404


def test_user_search_and_validation(make_user):
    a, _ = make_user("alice"), make_user("bobby")
    assert a.get("/api/users/search?q=bo").get_json()["users"][0]["username"] == "bobby"
    assert a.get("/api/users/search?q=a").get_json()["users"] == []
    assert a.post("/api/rooms", json={"name": "x"}).status_code == 400
