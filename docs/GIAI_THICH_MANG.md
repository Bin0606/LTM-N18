# Giải thích phần mạng (phục vụ bảo vệ)

**TCP/IP dùng như thế nào?** Trình duyệt và server nói chuyện bằng TCP trên IP: IP định tuyến gói tin tới máy chủ (Render), TCP tạo kết nối tin cậy (bắt tay 3 bước, đánh số, truyền lại gói mất). HTTP, TLS và WebSocket đều chạy *trên* một kết nối TCP.

**HTTP khác WebSocket?** HTTP là hỏi–đáp: client gửi request, server trả response rồi (thường) kết thúc; server không tự đẩy dữ liệu. WebSocket bắt đầu bằng một request HTTP có `Upgrade: websocket`, server trả `101 Switching Protocols`, sau đó cùng kết nối TCP đó trở thành kênh **hai chiều, giữ mở**; mỗi bên gửi frame bất cứ lúc nào, header nhỏ nên nhanh hơn polling. ConnectHub dùng HTTP cho đăng nhập, REST, upload/download file và WebSocket cho tin nhắn, presence.

**Client kết nối server thế nào?** Đăng nhập HTTPS → server đặt cookie phiên. `io()` ở `chat.js` mở `wss://host/socket.io/...` kèm cookie; `on_connect` trong `app/sockets/chat.py` kiểm tra `current_user` và Origin, từ chối nếu chưa đăng nhập.

**Server quản lý nhiều người dùng thế nào?** gunicorn `gthread` (1 worker, nhiều thread) giữ mỗi kết nối WebSocket; Socket.IO gán mỗi kết nối một `sid`. `presence.py` đếm số kết nối theo user để biết Online/Offline, nên một người mở nhiều thiết bị vẫn đúng.

**Tin nhắn đến đúng phòng thế nào?** Mỗi phòng là một Socket.IO room `room:<id>`. Khi kết nối, server tự cho `sid` vào các room mà DB xác nhận user là thành viên. `emit("message:new", ..., to="room:<id>")` chỉ gửi cho các `sid` trong room đó. Mỗi lần gửi, server kiểm tra lại quyền thành viên, nên không thể chèn tin vào phòng khác.

**Truyền và lưu file thế nào?** Client gửi `multipart/form-data` qua HTTP (có thanh tiến trình bằng `XMLHttpRequest`). Server kiểm tra quyền, đuôi, MIME, nội dung, dung lượng, lưu vào storage với tên ngẫu nhiên, ghi metadata vào `shared_files`, tạo tin nhắn loại `file` và phát qua WebSocket. Tải về qua `GET /files/<id>` luôn kiểm tra thành viên phòng.

**Hoạt động qua Internet thế nào?** Render cấp domain `*.onrender.com` (DNS → IP của Render), chấm dứt TLS (HTTPS/WSS) tại reverse proxy rồi chuyển tiếp HTTP/WebSocket vào Flask. `ProxyFix` giúp Flask biết scheme/host thật; `CORS_ORIGINS` giới hạn origin được mở WebSocket. Cả máy tính lẫn điện thoại dùng chung một URL, một tài khoản; dữ liệu nằm ở PostgreSQL và file storage nên đồng bộ giữa mọi thiết bị.
