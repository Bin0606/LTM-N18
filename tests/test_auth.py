from app.models import User
from conftest import login, register


def test_register_success_hashes_password(client, app):
    r = register(client, "alice")
    assert r.status_code == 302
    with app.app_context():
        user = User.query.filter_by(username="alice").one()
        assert user.password_hash != "Passw0rd!" and user.check_password("Passw0rd!")


def test_register_duplicates_and_validation(client):
    register(client, "alice")
    assert register(client, "alice", email="other@example.com").status_code == 400  # username trùng
    assert register(client, "bob", email="alice@example.com").status_code == 400    # email trùng
    assert register(client, "carol", password="short").status_code == 400            # mật khẩu yếu
    assert register(client, "x!").status_code == 400                                 # username sai định dạng
    r = client.post("/register", data={"full_name": "A B", "username": "dave", "email": "bad",
                                       "password": "Passw0rd!", "confirm": "Passw0rd!"})
    assert r.status_code == 400


def test_login_by_username_and_email_and_wrong_password(client):
    register(client, "alice")
    assert login(client, "alice", "wrong").status_code == 401
    assert login(client, "alice").status_code == 302
    c2 = client.application.test_client()
    assert login(c2, "alice@example.com").status_code == 302


def test_private_area_requires_login(client):
    assert client.get("/").status_code == 302
    r = client.get("/api/rooms")
    assert r.status_code == 401 and r.get_json()["error"]


def test_login_brute_force_lock(client):
    register(client, "alice")
    for _ in range(8):
        assert login(client, "alice", "bad").status_code == 401
    assert login(client, "alice").status_code == 429  # bị khóa tạm thời kể cả khi đúng mật khẩu


def test_logout(client):
    register(client, "alice"); login(client, "alice")
    assert client.post("/logout").status_code == 302
    assert client.get("/api/rooms").status_code == 401


def test_open_redirect_blocked(client):
    register(client, "alice")
    r = client.post("/login?next=//evil.com", data={"identifier": "alice", "password": "Passw0rd!"})
    assert r.status_code == 302 and "evil.com" not in r.headers["Location"]
