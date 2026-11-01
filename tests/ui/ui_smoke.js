// Smoke test giao diện: chạy chat.js THẬT trong jsdom, nối vào server đang chạy (HTTP + WebSocket thật).
// Dùng: cd tests/ui && npm install jsdom@24 && node ui_smoke.js http://127.0.0.1:5000
const { JSDOM, CookieJar } = require("jsdom");
const BASE = (process.argv[2] || "http://127.0.0.1:5000").replace(/\/$/, "");
const results = [];
const check = (n, ok, d) => { results.push(ok); console.log((ok ? "PASS " : "FAIL ") + n + (!ok && d ? "  [" + d + "]" : "")); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 6000) { const end = Date.now() + ms; while (Date.now() < end) { if (fn()) return true; await sleep(60); } return false; }

async function signup(tag) {
  const jar = new Map();
  const call = async (path, init = {}) => {
    const headers = Object.assign({}, init.headers, jar.size ? { Cookie: [...jar].map(([k, v]) => k + "=" + v).join("; ") } : {});
    const r = await fetch(BASE + path, Object.assign({}, init, { headers, redirect: "manual" }));
    for (const c of r.headers.getSetCookie()) { const [kv] = c.split(";"); const i = kv.indexOf("="); jar.set(kv.slice(0, i), kv.slice(i + 1)); }
    return r;
  };
  const csrf = async (p) => (await (await call(p)).text()).match(/name="csrf-token" content="([^"]+)"/)[1];
  const name = "uichk" + tag + Math.random().toString(36).slice(2, 8), pw = "Passw0rd123";
  let t = await csrf("/register");
  await call("/register", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ csrf_token: t, full_name: "UI " + tag, username: name, email: name + "@example.com", password: pw, confirm: pw }) });
  t = await csrf("/login");
  await call("/login", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ csrf_token: t, identifier: name, password: pw }) });
  const token = await csrf("/");
  return { name, jar, call, token, cookie: () => [...jar].map(([k, v]) => k + "=" + v).join("; ") };
}

(async () => {
  const me = await signup("a"), other = await signup("b");
  const cookieJar = new CookieJar();
  for (const [k, v] of me.jar) cookieJar.setCookieSync(`${k}=${v}; Path=/`, BASE);
  const dom = await JSDOM.fromURL(BASE + "/", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, cookieJar,
    beforeParse(w) {
      w.matchMedia = w.matchMedia || ((q) => ({ matches: /min-width/.test(q), addEventListener() {}, removeEventListener() {} }));
      w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
      w.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
      w.HTMLElement.prototype.scrollIntoView = () => {};
      const headersFor = () => ({ Cookie: me.cookie() });
      w.fetch = (url, init = {}) => fetch(new URL(url, BASE), Object.assign({}, init, { headers: Object.assign({}, init.headers, headersFor()) }));
    },
  });
  const w = dom.window, d = w.document, $ = (s) => d.querySelector(s);
  const errors = []; w.addEventListener("error", (e) => errors.push(e.message));
  check("trang chat tải xong, chat.js khởi chạy", await until(() => $("#me-name") && $("#me-name").textContent.startsWith("UI a")));
  check("chưa có phòng: hiện trạng thái trống", $("#room-list").textContent.includes("Chưa có phòng"));

  // Tạo phòng qua giao diện
  $("#btn-create-room").click();
  $("#create-name").value = "Phòng UI <b>test</b>"; $("#create-desc").value = "mô tả";
  $("#form-create").dispatchEvent(new w.Event("submit", { cancelable: true, bubbles: true }));
  check("tạo phòng → xuất hiện ở sidebar và mở phòng",
    await until(() => $("#room-title").textContent === "# Phòng UI <b>test</b>" && !$("#composer").hidden));
  check("tên phòng chứa HTML được hiển thị dạng chữ (không thành thẻ <b>)", !$("#room-list b") && !$("#room-title b"));
  check("panel phải hiện link mời + mã", $("#invite-link").value.includes("/join/") && $("#invite-code").value.length >= 12);

  // Gửi tin nhắn (chứa HTML → phải hiển thị nguyên văn)
  const xss = '<img src=x onerror="window.__pwned=1"> Xin chào';
  $("#msg-input").value = xss; $("#btn-send").click();
  check("tin nhắn gửi xong được server xác nhận (không còn trạng thái pending)",
    await until(() => d.querySelectorAll("#message-list .msg:not(.pending)").length === 1 && !d.querySelector(".msg.pending")));
  const bubble = d.querySelector("#message-list .msg .text");
  check("nội dung hiển thị nguyên văn, không tạo thẻ <img>", bubble && bubble.textContent === xss && !d.querySelector("#message-list img") && !w.__pwned);
  check("không nhân đôi tin (ack + sự kiện WebSocket)", d.querySelectorAll("#message-list .msg").length === 1);

  // Người dùng khác tham gia bằng mã và nhắn tin → UI nhận realtime
  const code = $("#invite-code").value;
  await other.call("/api/rooms/join", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": other.token }, body: JSON.stringify({ code }) });
  check("thông báo hệ thống 'đã tham gia phòng' hiện realtime", await until(() => $("#message-list").textContent.includes("đã tham gia phòng")));
  check("danh sách thành viên cập nhật (2 người)", await until(() => $("#member-count").textContent === "2"));
  const { io } = (() => { const m = require.resolve("socket.io-client"); return require(m); })();
  const sock = io(BASE, { transports: ["websocket"], extraHeaders: { Cookie: other.cookie() } });
  await new Promise((r) => sock.on("connect", r));
  const roomId = parseInt((await (await me.call("/api/rooms")).json()).rooms[0].id, 10);
  sock.emit("message:send", { room_id: roomId, content: "Chào từ người kia", client_msg_id: "o1" });
  check("tin của người khác hiển thị realtime ở bên trái", await until(() => [...d.querySelectorAll("#message-list .msg:not(.own) .text")].some((n) => n.textContent === "Chào từ người kia")));
  check("chấm Online của thành viên", await until(() => d.querySelectorAll("#member-list .dot.on").length === 2));
  sock.disconnect();
  check("thành viên chuyển Offline realtime", await until(() => d.querySelectorAll("#member-list .dot.on").length === 1));

  // Upload qua UI (XHR) — jsdom hỗ trợ XHR+FormData+File
  const file = new w.File([Buffer.from("Xin chào tệp txt")], "ghi chú.txt", { type: "text/plain" });
  const dt = { files: [file] };
  const input = $("#file-input"); Object.defineProperty(input, "files", { value: [file], configurable: true });
  input.dispatchEvent(new w.Event("change", { bubbles: true }));
  check("upload qua giao diện → có thẻ file trong chat", await until(() => d.querySelector("#message-list .file-card .fname") && d.querySelector("#message-list .file-card .fname").textContent === "ghi chú.txt", 8000));
  check("danh sách file bên phải cập nhật", await until(() => $("#file-list").textContent.includes("ghi chú.txt")));
  const badExt = new w.File(["x"], "virus.exe"); Object.defineProperty(input, "files", { value: [badExt], configurable: true });
  input.dispatchEvent(new w.Event("change", { bubbles: true }));
  check("client chặn định dạng không hỗ trợ bằng toast lỗi", await until(() => d.querySelector(".toast.error")));

  check("không có lỗi JavaScript chưa bắt", errors.length === 0, errors.join(" | "));
  const failed = results.filter((x) => !x).length;
  console.log(`\n${results.length - failed}/${results.length} đạt`);
  w.close(); process.exit(failed ? 1 : 0);
})().catch((e) => { console.error("LỖI TEST:", e); process.exit(2); });
