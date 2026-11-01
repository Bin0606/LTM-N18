# Báo cáo kiểm thử (chỉ ghi những gì ĐÃ chạy thật)

Môi trường: Linux, Python 3.12.3, Flask 3.1.3, Flask-SocketIO 5.7.0, gunicorn (`gthread`, 1 worker, 100 thread), PostgreSQL 16.2 (bản nhúng qua `pgserver`), SQLite, S3 giả lập (`moto`).
Ngày chạy: 31/10/2026.

## Đã chạy và đạt

| Bộ kiểm thử | Kết quả | Phạm vi |
|---|---|---|
| `pytest` (tests/) | **151/151 đạt** | Toàn bộ chức năng và các điểm gia cố, chi tiết ở bảng dưới |
| `tests/live_check.py` trên **gunicorn thật + PostgreSQL 16.2 + S3 giả lập qua mạng (moto_server)** | **43/43 đạt** | WebSocket thật, chat nhóm, DM 1-1, đánh dấu đã đọc, upload/download, quyền truy cập, kết nối lại, giới hạn upload 429, open redirect, presence nhiều thiết bị, đăng xuất ngắt WebSocket |
| `tests/ui/ui_smoke.js` (jsdom chạy `chat.js` thật) trên cùng stack | **17/17 đạt** | Tạo phòng, gửi tin, chống XSS, tin/presence realtime, upload qua UI, chặn định dạng sai, không lỗi JS |
| Storage chết giữa chừng (tắt server S3 khi ứng dụng đang chạy) | Đạt | Tải file → **503** + `Retry-After`; upload → **503**; các API khác vẫn 200 (không còn gộp thành 404/500) |
| Migration trên PostgreSQL 16.2 | Đạt | Tạo 5 bảng, khóa ngoại `ON DELETE CASCADE` (chạy trước đó và lặp lại trong lần chạy live) |

### Phạm vi `pytest` theo yêu cầu

| Hạng mục | Test |
|---|---|
| Đăng ký, đăng nhập, đăng xuất | `test_auth.py` |
| Tạo/tham gia/rời/xóa phòng, phân quyền admin/member, kick | `test_rooms.py` |
| Chat nhóm realtime, tin ở đúng phòng, validate, rate limit, lỗi DB, presence nhiều thiết bị | `test_socket.py` |
| Kết nối lại WebSocket (lấy lại tin lỡ, không trùng tin) | `test_socket.py::test_reconnect_resync_and_no_duplicates`, `live_check.py` |
| Chat riêng 1–1 và race hai người cùng mở DM | `test_rooms.py`, `test_hardening.py::test_dm_race_returns_existing_room` |
| Đánh dấu đã đọc: hợp lệ, ID giả/không tồn tại/sai kiểu/phòng khác, không lùi | `test_hardening.py` (11 + 3 test) |
| Giới hạn upload 5 file/60 giây/tài khoản, 429 + Retry-After, không ảnh hưởng người khác, hết cửa sổ thì dùng lại được | `test_hardening.py` |
| Dữ liệu đầu vào sai (null, số, bool, list, dict, JSON hỏng, chuỗi dài, ký tự điều khiển) không gây 500 | `test_hardening.py` (~90 trường hợp) |
| Open redirect `next` (27 biến thể độc hại, 4 đường dẫn hợp lệ) | `test_hardening.py` |
| Chống dò đăng nhập (đổi username, tấn công phân tán, không lộ tài khoản tồn tại), spam đăng ký, giới hạn bộ nhớ | `test_hardening.py` |
| Upload/download, phân quyền tải (thành viên, người ngoài, chưa đăng nhập, đã rời, bị kick), header an toàn, dọn file khi xóa phòng | `test_files.py`, `test_hardening.py` |
| Lỗi storage: không tồn tại = 404, storage lỗi = 503, upload khi storage lỗi không để lại bản ghi | `test_hardening.py`, `test_storage_s3.py` |
| S3: phân loại lỗi (NoSuchKey, NoSuchBucket, AccessDenied, SlowDown, mất mạng), cấu hình checksum tương thích R2, thiếu cấu hình thì báo lỗi lúc khởi động, end-to-end qua ứng dụng | `test_storage_s3.py` |

## Lỗi tìm thấy khi kiểm thử và đã sửa
- `/users/<id>/avatar` khi chưa đăng nhập trả 302 về trang login thay vì 401 như các endpoint dữ liệu khác.
- Trong lần chạy đầu của `live_check` với S3, bucket giả lập tạo sai region (lỗi của script chạy thử, không phải của app). App đã trả đúng 503 thay vì 404/500.
- Một số lỗi nằm ở chính test (tên phòng/username quá ngắn, tên bucket, kiểm tra nhầm hộp thư sau khi kết nối lại, cổng 5060 bị Node `fetch` chặn); đã sửa test.

## CHƯA kiểm thử (không làm được trong môi trường này hoặc cần tài khoản thật)
- **Neon thật**: chỉ thử trên PostgreSQL 16.2 cục bộ. Chưa thử đánh thức compute sau khi tạm dừng, chưa thử chuỗi pooled/direct, `sslmode=require` và `channel_binding=require` của Neon.
- **Cloudflare R2 thật**: chỉ thử với S3 giả lập (moto, kể cả qua mạng cục bộ). Chưa thử chữ ký/checksum với R2 thật, hạn mức, hay cần thẻ thanh toán hay không.
- **Render thật**: chưa deploy, chưa kiểm chứng `render.yaml` (`plan: free`, `sync: false`, lệnh khởi động có `ENV=... flask db upgrade && gunicorn`), HTTPS/WSS, hành vi ngủ/đánh thức với WebSocket.
- **Giao diện desktop và mobile**: **chưa chạy trên trình duyệt thật** (Chrome Windows/Android, Safari iPhone). jsdom không vẽ bố cục, nên chưa kiểm chứng drawer, `100dvh`, bàn phím ảo. Chỉ có kiểm tra logic JavaScript.
- Chưa thử từ hai mạng Internet khác nhau, chưa load test nhiều người đồng thời, chưa thử nhiều worker/Redis (chưa cài đặt).

## Checklist thử thủ công trên thiết bị (điền kết quả khi làm)

| # | Việc cần thử | Kết quả |
|---|---|---|
| 1 | Chrome Windows: đăng ký, đăng nhập, tạo phòng, chat với thiết bị khác | |
| 2 | Chrome Android (360px): mở/đóng sidebar, mở danh sách thành viên, ô nhập không bị bàn phím che | |
| 3 | Safari iPhone: như trên, kiểm tra thanh địa chỉ/notch | |
| 4 | Điện thoại: chụp ảnh/chọn file upload, tải file về | |
| 5 | Cùng tài khoản đăng nhập máy tính + điện thoại: tin nhắn đồng bộ, Online đúng khi tắt một thiết bị | |
| 6 | Máy tính (Wi-Fi) và điện thoại (4G) cùng vào một phòng | |
| 7 | Tắt Wi-Fi 10 giây rồi bật lại: banner kết nối, tin nhắn đồng bộ lại | |
| 8 | Upload liên tục 6 file trong 1 phút: file thứ 6 báo lỗi vượt giới hạn | |
| 9 | Sau khi ngủ dậy (Render Free) và sau redeploy: tin nhắn và file cũ còn nguyên | |
