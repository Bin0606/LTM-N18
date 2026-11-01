/* ConnectHub - giao diện chat.
 * Quy tắc an toàn: mọi nội dung do người dùng gửi (tin nhắn, tên, tên file) chỉ được gán bằng textContent,
 * KHÔNG BAO GIỜ dùng innerHTML → chống XSS. Dữ liệu chat luôn nằm ở server, trình duyệt chỉ giữ trạng thái hiển thị. */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var app = $("#app");
  var CSRF = $('meta[name="csrf-token"]').content;
  var MAX_UPLOAD_MB = parseInt(app.dataset.maxUploadMb, 10) || 10;
  var ALLOWED_EXT = ["jpg", "jpeg", "png", "webp", "pdf", "docx", "xlsx", "txt"];

  var state = {
    me: null, rooms: [], room: null, members: [], messages: [], hasMore: false,
    pending: {}, socket: null, connectedBefore: false, unreadTotal: 0, nearBottom: true,
    uploadXhr: null, readTimer: null
  };

  /* ---------- Tiện ích ---------- */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }
  function fmtTime(iso) { return new Date(iso).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" }); }
  function fmtDay(iso) { return new Date(iso).toLocaleDateString("vi-VN", { weekday: "long", day: "2-digit", month: "2-digit", year: "numeric" }); }
  function dayKey(iso) { var d = new Date(iso); return d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate(); }
  function fmtSize(n) {
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }
  function initials(name) {
    var parts = (name || "?").trim().split(/\s+/);
    return ((parts[0][0] || "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
  }
  function setAvatar(node, user) {
    node.textContent = "";
    if (user && user.avatar_url) {
      var img = document.createElement("img");
      img.alt = ""; img.src = user.avatar_url; img.loading = "lazy";
      node.appendChild(img);
    } else {
      node.textContent = initials(user && user.full_name);
    }
  }
  function debounce(fn, ms) {
    var t; return function () { var a = arguments, c = this; clearTimeout(t); t = setTimeout(function () { fn.apply(c, a); }, ms); };
  }
  function toast(title, body, isError) {
    var t = el("div", "toast" + (isError ? " error" : ""));
    if (title) t.appendChild(el("b", null, title));
    if (body) t.appendChild(el("span", null, body));
    $("#toasts").appendChild(t);
    setTimeout(function () { t.remove(); }, isError ? 6000 : 4000);
  }
  function api(path, opts) {
    opts = opts || {};
    var headers = { "X-CSRFToken": CSRF };
    var init = { method: opts.method || "GET", headers: headers, credentials: "same-origin" };
    if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.json); }
    return fetch(path, init).then(function (res) {
      if (res.status === 401) { window.location.href = "/login"; throw new Error("Phiên đăng nhập đã hết hạn."); }
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) { var e = new Error(data.error || "Có lỗi xảy ra (" + res.status + ")."); e.status = res.status; throw e; }
        return data;
      });
    }, function () { throw new Error("Không kết nối được máy chủ. Hãy kiểm tra mạng."); });
  }
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea"); ta.value = text; ta.setAttribute("readonly", "");
      ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy") ? resolve() : reject(); } catch (e) { reject(e); } ta.remove();
    });
  }

  /* ---------- Drawer (mobile) và hộp thoại ---------- */
  function closeDrawers() {
    $("#left").classList.remove("open"); $("#right").classList.remove("open"); $("#backdrop").hidden = true;
  }
  function openDrawer(id) { $(id).classList.add("open"); $("#backdrop").hidden = false; }
  $("#btn-menu").addEventListener("click", function () { openDrawer("#left"); });
  $("#btn-empty-menu").addEventListener("click", function () { openDrawer("#left"); });
  $("#btn-members").addEventListener("click", function () { openDrawer("#right"); });
  $("#backdrop").addEventListener("click", closeDrawers);
  document.querySelectorAll("[data-close-drawer]").forEach(function (b) { b.addEventListener("click", closeDrawers); });
  document.querySelectorAll("[data-dlg-close]").forEach(function (b) { b.addEventListener("click", function () { b.closest("dialog").close(); }); });
  $("#btn-theme").addEventListener("click", function () { window.toggleTheme(); });

  function dlgError(form, msg) { var p = $(".dlg-error", form); if (p) p.textContent = msg || ""; }
  function confirmDialog(title, text, onOk) {
    $("#confirm-title").textContent = title; $("#confirm-text").textContent = text;
    var dlg = $("#dlg-confirm"), ok = $("#confirm-ok");
    ok.onclick = function () { dlg.close(); onOk(); };
    dlg.showModal();
  }

  /* ---------- Trạng thái kết nối ---------- */
  var bannerTimer = null;
  function setBanner(kind, text) {
    var b = $("#conn-banner"); clearTimeout(bannerTimer);
    if (!kind) { b.hidden = true; return; }
    b.className = "conn-banner " + kind; b.textContent = text; b.hidden = false;
    if (kind === "ok") bannerTimer = setTimeout(function () { b.hidden = true; }, 2500);
  }

  /* ---------- Danh sách phòng ---------- */
  function totalUnread() { return state.rooms.reduce(function (s, r) { return s + (r.unread || 0); }, 0); }
  function updateTitle() { var n = totalUnread(); document.title = (n ? "(" + n + ") " : "") + "ConnectHub"; }

  function roomItem(r) {
    var li = el("li"), b = el("button", "room-item" + (state.room && state.room.id === r.id ? " active" : ""));
    b.type = "button";
    if (r.is_direct && r.peer) { var av = el("span", "avatar"); setAvatar(av, r.peer); b.appendChild(av); }
    b.appendChild(el("span", "name", (r.is_direct ? "" : "# ") + r.room_name));
    if (r.is_direct && r.peer) b.appendChild(el("span", "dot" + (r.peer.online ? " on" : "")));
    if (r.unread) b.appendChild(el("span", "badge", r.unread > 99 ? "99+" : String(r.unread)));
    b.addEventListener("click", function () { openRoom(r.id); closeDrawers(); });
    li.appendChild(b); return li;
  }
  function renderRooms() {
    var filter = $("#side-search").value.trim().toLowerCase();
    var rl = $("#room-list"), dl = $("#dm-list"); rl.textContent = ""; dl.textContent = "";
    state.rooms.forEach(function (r) {
      if (filter && r.room_name.toLowerCase().indexOf(filter) === -1) return;
      (r.is_direct ? dl : rl).appendChild(roomItem(r));
    });
    if (!rl.children.length) rl.appendChild(el("li", "empty", filter ? "Không có phòng phù hợp." : "Chưa có phòng nào."));
    if (!dl.children.length) dl.appendChild(el("li", "empty", filter ? "Không có kết quả." : "Chưa có cuộc trò chuyện nào."));
    updateTitle();
  }
  function loadRooms() {
    return api("/api/rooms").then(function (d) {
      state.rooms = d.rooms;
      if (state.room) {  // giữ số chưa đọc của phòng đang mở = 0
        state.rooms.forEach(function (r) { if (r.id === state.room.id && !document.hidden) r.unread = 0; });
      }
      renderRooms();
    });
  }

  /* ---------- Hiển thị tin nhắn ---------- */
  function fileIcon(f) { return f.is_image ? "🖼" : f.mime_type === "application/pdf" ? "📕" : /sheet/.test(f.mime_type) ? "📗" : /word/.test(f.mime_type) ? "📘" : "📄"; }

  function buildMessage(m, prev) {
    var frag = document.createDocumentFragment();
    if (!prev || dayKey(prev.created_at) !== dayKey(m.created_at)) frag.appendChild(el("div", "day-sep", fmtDay(m.created_at)));
    if (m.type === "system") { frag.appendChild(el("div", "sys-msg", m.content)); return frag; }
    var own = m.sender.id === state.me.id;
    var row = el("div", "msg" + (own ? " own" : "")); row.dataset.id = m.id || ""; if (m.client_msg_id) row.dataset.cid = m.client_msg_id;
    var av = el("span", "avatar"); setAvatar(av, m.sender); row.appendChild(av);
    var bubble = el("div", "bubble");
    if (!own && state.room && !state.room.is_direct) bubble.appendChild(el("div", "who", m.sender.full_name));
    if (m.type === "file" && m.file) {
      var f = m.file;
      if (f.is_image) {
        var link = document.createElement("a"); link.href = f.url; link.target = "_blank"; link.rel = "noopener";
        var img = el("img", "preview"); img.src = f.url; img.alt = f.name; img.loading = "lazy"; link.appendChild(img); bubble.appendChild(link);
      }
      var card = document.createElement("a"); card.className = "file-card"; card.href = f.url; card.setAttribute("download", f.name);
      card.appendChild(el("span", "ico", fileIcon(f)));
      var info = el("span"); info.appendChild(el("div", "fname", f.name)); info.appendChild(el("small", null, fmtSize(f.size) + " · Tải xuống"));
      card.appendChild(info); bubble.appendChild(card);
    } else {
      bubble.appendChild(el("div", "text", m.content));
    }
    var meta = el("div", "meta", m.created_at ? fmtTime(m.created_at) : "");
    if (own && m.id) meta.textContent += " · Đã gửi";
    bubble.appendChild(meta); row.appendChild(bubble); frag.appendChild(row);
    return frag;
  }
  function renderMessages() {
    var list = $("#message-list"); list.textContent = "";
    state.messages.forEach(function (m, i) { list.appendChild(buildMessage(m, state.messages[i - 1])); });
    $("#btn-load-more").hidden = !state.hasMore;
  }
  function scrollBottom() { var s = $("#messages"); s.scrollTop = s.scrollHeight; $("#btn-new-msgs").hidden = true; }
  $("#messages").addEventListener("scroll", function () {
    var s = $("#messages"); state.nearBottom = s.scrollHeight - s.scrollTop - s.clientHeight < 80;
    if (state.nearBottom) $("#btn-new-msgs").hidden = true;
  });
  $("#btn-new-msgs").addEventListener("click", scrollBottom);

  function lastMessageId() { for (var i = state.messages.length - 1; i >= 0; i--) if (state.messages[i].id) return state.messages[i].id; return 0; }
  function addMessage(m) {
    if (state.messages.some(function (x) { return x.id === m.id; })) return false;
    var pend = m.client_msg_id && state.pending[m.client_msg_id];
    if (pend) { pend.node.remove(); delete state.pending[m.client_msg_id]; }
    var prev = state.messages[state.messages.length - 1];
    state.messages.push(m);
    var node = buildMessage(m, prev), own = m.sender.id === state.me.id;
    $("#message-list").appendChild(node);
    if (own || state.nearBottom) scrollBottom(); else $("#btn-new-msgs").hidden = false;
    return true;
  }
  function scheduleRead() {
    clearTimeout(state.readTimer);
    state.readTimer = setTimeout(function () {
      if (!state.room || document.hidden) return;
      var id = lastMessageId(); if (!id) return;
      api("/api/rooms/" + state.room.id + "/read", { method: "POST", json: { message_id: id } }).catch(function () {});
      state.rooms.forEach(function (r) { if (r.id === state.room.id) r.unread = 0; });
      renderRooms();
    }, 400);
  }

  /* ---------- Mở phòng ---------- */
  function setLoading(on) { $("#loading").hidden = !on; }
  function openRoom(id) {
    state.room = { id: id }; state.messages = []; state.pending = {}; state.hasMore = false;
    renderRooms();
    $("#empty-state").hidden = true; $("#messages").hidden = false; $("#composer").hidden = false;
    $("#btn-room-search").hidden = false; $("#btn-members").hidden = false; $("#right").hidden = false; app.classList.add("has-room");
    $("#search-panel").hidden = true; $("#message-list").textContent = ""; setLoading(true);
    try { localStorage.setItem("lastRoom", String(id)); } catch (e) {}
    return Promise.all([api("/api/rooms/" + id), api("/api/rooms/" + id + "/messages?limit=30")]).then(function (res) {
      if (!state.room || state.room.id !== id) return;
      state.room = res[0].room; state.members = res[0].room.members;
      state.messages = res[1].messages; state.hasMore = res[1].has_more;
      setLoading(false); renderHeader(); renderMembers(); renderMessages(); scrollBottom(); loadFiles(); scheduleRead();
      $("#msg-input").focus({ preventScroll: true });
    }).catch(function (e) {
      setLoading(false); toast("Không mở được phòng", e.message, true);
      if (e.status === 403 || e.status === 404) { state.room = null; showEmpty(); loadRooms(); }
    });
  }
  function showEmpty() {
    $("#empty-state").hidden = false; $("#messages").hidden = true; $("#composer").hidden = true; $("#search-panel").hidden = true;
    $("#btn-room-search").hidden = true; $("#btn-members").hidden = true; $("#right").hidden = true; app.classList.remove("has-room");
    $("#room-title").textContent = "ConnectHub"; $("#room-desc").textContent = ""; closeDrawers();
  }
  function renderHeader() {
    var r = state.room;
    $("#room-title").textContent = (r.is_direct ? "" : "# ") + r.room_name;
    $("#room-desc").textContent = r.is_direct ? (r.peer && r.peer.status_text) || "Cuộc trò chuyện cá nhân" : (r.description || "Chưa có mô tả");
    $("#info-desc").textContent = r.is_direct ? "Cuộc trò chuyện riêng giữa hai người." : (r.description || "Chưa có mô tả");
    $("#info-created").textContent = new Date(r.created_at).toLocaleDateString("vi-VN");
    $("#invite-box").hidden = r.is_direct;
    if (!r.is_direct) {
      $("#invite-link").value = location.origin + "/join/" + r.invite_code; $("#invite-code").value = r.invite_code;
    }
    $("#btn-edit-room").hidden = r.is_direct || r.role !== "admin";
    $("#btn-delete-room").hidden = r.is_direct || r.owner_id !== state.me.id;
    $("#btn-leave-room").hidden = r.is_direct || r.owner_id === state.me.id;
  }
  function renderMembers() {
    var ul = $("#member-list"); ul.textContent = ""; $("#member-count").textContent = state.members.length;
    var isAdmin = state.room && state.room.role === "admin";
    state.members.forEach(function (m) {
      var li = el("li"), av = el("span", "avatar"); setAvatar(av, m); li.appendChild(av);
      var info = el("span", "info"); info.appendChild(el("b", null, m.full_name + (m.id === state.me.id ? " (bạn)" : "")));
      info.appendChild(el("small", null, m.status_text || "@" + m.username)); li.appendChild(info);
      if (m.role === "admin") li.appendChild(el("span", "tag", "Admin"));
      var dot = el("span", "dot" + (m.online ? " on" : "")); dot.title = m.online ? "Online" : "Offline"; dot.dataset.uid = m.id; li.appendChild(dot);
      if (isAdmin && m.id !== state.me.id && m.id !== state.room.owner_id && !state.room.is_direct) {
        var k = el("button", "icon-btn", "✕"); k.type = "button"; k.title = "Xóa khỏi phòng"; k.setAttribute("aria-label", "Xóa " + m.full_name + " khỏi phòng");
        k.addEventListener("click", function () {
          confirmDialog("Xóa thành viên", "Xóa " + m.full_name + " khỏi phòng?", function () {
            api("/api/rooms/" + state.room.id + "/members/" + m.id, { method: "DELETE" }).catch(function (e) { toast("Lỗi", e.message, true); });
          });
        });
        li.appendChild(k);
      }
      ul.appendChild(li);
    });
  }
  function refreshMembers() {
    if (!state.room) return;
    var id = state.room.id;
    api("/api/rooms/" + id).then(function (d) {
      if (!state.room || state.room.id !== id) return;
      var keep = state.room; state.room = d.room; state.members = d.room.members; renderHeader(); renderMembers(); void keep;
    }).catch(function () {});
  }
  var loadFiles = function () {
    if (!state.room) return;
    var id = state.room.id, q = $("#file-filter").value.trim();
    api("/api/rooms/" + id + "/files" + (q ? "?q=" + encodeURIComponent(q) : "")).then(function (d) {
      if (!state.room || state.room.id !== id) return;
      var ul = $("#file-list"); ul.textContent = "";
      if (!d.files.length) ul.appendChild(el("li", "muted small", q ? "Không tìm thấy file." : "Chưa có file nào."));
      d.files.forEach(function (f) {
        var li = el("li"), a = document.createElement("a"); a.href = f.url; a.setAttribute("download", f.name);
        a.appendChild(el("span", null, fileIcon(f) + " " + f.name)); a.appendChild(el("small", null, fmtSize(f.size) + " · " + (f.uploader || "")));
        li.appendChild(a); ul.appendChild(li);
      });
    }).catch(function () {});
  };
  var loadFilesDebounced = debounce(loadFiles, 300);
  $("#file-filter").addEventListener("input", loadFilesDebounced);

  $("#btn-load-more").addEventListener("click", function () {
    if (!state.room || !state.messages.length) return;
    var first = state.messages[0].id, id = state.room.id, box = $("#messages"), prevH = box.scrollHeight;
    api("/api/rooms/" + id + "/messages?limit=30&before_id=" + first).then(function (d) {
      if (!state.room || state.room.id !== id) return;
      state.messages = d.messages.concat(state.messages); state.hasMore = d.has_more; renderMessages();
      box.scrollTop = box.scrollHeight - prevH;
    }).catch(function (e) { toast("Lỗi", e.message, true); });
  });

  /* ---------- Gửi tin nhắn (qua WebSocket, chỉ hiển thị "đã gửi" sau khi server xác nhận) ---------- */
  var input = $("#msg-input");
  function autoGrow() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 140) + "px"; }
  input.addEventListener("input", autoGrow);
  input.addEventListener("keydown", function (e) {
    var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    if (e.key === "Enter" && !e.shiftKey && !coarse) { e.preventDefault(); sendMessage(); }
  });
  $("#btn-send").addEventListener("click", sendMessage);

  function askNotifyPermission() {
    if ("Notification" in window && Notification.permission === "default") { try { Notification.requestPermission(); } catch (e) {} }
  }
  function markFailed(entry, reason) {
    entry.node.classList.remove("pending"); entry.node.classList.add("failed");
    var meta = $(".meta", entry.node); meta.textContent = reason + " ";
    var retry = el("button", "retry", "Gửi lại"); retry.type = "button";
    retry.addEventListener("click", function () { entry.node.classList.remove("failed"); entry.node.classList.add("pending"); transmit(entry); });
    meta.appendChild(retry);
  }
  function transmit(entry) {
    var meta = $(".meta", entry.node); meta.textContent = "Đang gửi…";
    if (!state.socket || !state.socket.connected) { markFailed(entry, "Chưa kết nối."); return; }
    state.socket.timeout(10000).emit("message:send", { room_id: entry.room_id, content: entry.content, client_msg_id: entry.cid }, function (err, res) {
      if (err) { markFailed(entry, "Không nhận được xác nhận."); return; }
      if (!res || !res.ok) { markFailed(entry, (res && res.error) || "Gửi thất bại."); return; }
      if (state.room && state.room.id === res.message.room_id) addMessage(res.message);  // addMessage tự gỡ bong bóng "đang gửi"
    });
  }
  function sendMessage() {
    if (!state.room) return;
    var text = input.value.trim(); if (!text) return;
    askNotifyPermission();
    var cid = uuid();
    var fake = { id: 0, type: "text", content: text, created_at: new Date().toISOString(), client_msg_id: cid, sender: state.me };
    var node = buildMessage(fake, state.messages[state.messages.length - 1]);
    var row = node.querySelector ? node.querySelector(".msg") : null;
    $("#message-list").appendChild(node);
    var rows = $("#message-list").querySelectorAll(".msg[data-cid='" + cid + "']"); row = rows[rows.length - 1];
    row.classList.add("pending");
    var entry = { node: row, room_id: state.room.id, content: text, cid: cid };
    state.pending[cid] = entry; input.value = ""; autoGrow(); scrollBottom();
    transmit(entry);
  }

  /* ---------- Upload file (HTTP multipart, có tiến trình) ---------- */
  $("#btn-attach").addEventListener("click", function () { $("#file-input").click(); });
  $("#file-input").addEventListener("change", function (e) { var f = e.target.files[0]; e.target.value = ""; if (f) uploadFile(f); });
  $("#btn-upload-cancel").addEventListener("click", function () { if (state.uploadXhr) state.uploadXhr.abort(); });
  function uploadFile(file) {
    if (!state.room) return;
    var ext = (file.name.split(".").pop() || "").toLowerCase();
    if (ALLOWED_EXT.indexOf(ext) === -1) { toast("Định dạng không hỗ trợ", "Chỉ cho phép: " + ALLOWED_EXT.join(", ").toUpperCase(), true); return; }
    if (file.size > MAX_UPLOAD_MB * 1048576) { toast("File quá lớn", "Tối đa " + MAX_UPLOAD_MB + " MB mỗi file.", true); return; }
    if (state.uploadXhr) { toast("Đang upload", "Vui lòng chờ file hiện tại hoàn tất.", true); return; }
    var roomId = state.room.id, fd = new FormData(); fd.append("file", file);
    var xhr = new XMLHttpRequest(); state.uploadXhr = xhr;
    xhr.open("POST", "/api/rooms/" + roomId + "/files"); xhr.setRequestHeader("X-CSRFToken", CSRF);
    $("#upload-name").textContent = file.name; $("#upload-progress").value = 0; $("#upload-status").hidden = false;
    xhr.upload.onprogress = function (e) { if (e.lengthComputable) $("#upload-progress").value = Math.round(e.loaded / e.total * 100); };
    function done() { state.uploadXhr = null; $("#upload-status").hidden = true; }
    xhr.onload = function () {
      done(); var data = {}; try { data = JSON.parse(xhr.responseText); } catch (e) {}
      if (xhr.status === 401) { window.location.href = "/login"; return; }
      if (xhr.status >= 200 && xhr.status < 300) { if (state.room && state.room.id === roomId) { addMessage(data.message); scrollBottom(); } loadFiles(); }
      else toast("Upload thất bại", data.error || "Lỗi " + xhr.status, true);
    };
    xhr.onerror = function () { done(); toast("Upload thất bại", "Mất kết nối mạng. Hãy thử lại.", true); };
    xhr.onabort = function () { done(); toast("Đã hủy upload", file.name); };
    xhr.send(fd);
  }

  /* ---------- Socket.IO ---------- */
  function connectSocket() {
    var s = io({ transports: ["websocket", "polling"], reconnectionDelayMax: 5000 });
    state.socket = s;
    setBanner("warn", "Đang kết nối…");
    s.on("connect", function () {
      if (state.connectedBefore) {  // kết nối lại: đồng bộ phần tin nhắn bị lỡ từ server (nguồn dữ liệu duy nhất)
        setBanner("ok", "Đã kết nối lại");
        loadRooms(); resync();
      } else setBanner(null);
      state.connectedBefore = true;
      Object.keys(state.pending).forEach(function (cid) {  // thử gửi lại tin còn treo; server khử trùng bằng client_msg_id
        var p = state.pending[cid]; if (p.node.classList.contains("failed")) return; transmit(p);
      });
    });
    s.on("disconnect", function () { setBanner("offline", "Mất kết nối. Đang thử kết nối lại…"); });
    s.io.on("reconnect_attempt", function () { setBanner("warn", "Đang kết nối lại…"); });
    s.on("connect_error", function () { setBanner("offline", "Không kết nối được máy chủ. Đang thử lại…"); });
    s.on("message:new", onMessageNew);
    s.on("presence:update", function (p) {
      state.members.forEach(function (m) { if (m.id === p.user_id) m.online = p.online; });
      state.rooms.forEach(function (r) { if (r.peer && r.peer.id === p.user_id) r.peer.online = p.online; });
      renderMembers(); renderRooms();
    });
    s.on("rooms:changed", loadRooms);
    s.on("members:changed", function (p) { if (state.room && state.room.id === p.room_id) refreshMembers(); });
    s.on("room:updated", function (r) {
      state.rooms.forEach(function (x) { if (x.id === r.id) { x.room_name = r.room_name; x.description = r.description; } });
      if (state.room && state.room.id === r.id) { state.room.room_name = r.room_name; state.room.description = r.description; renderHeader(); }
      renderRooms();
    });
    s.on("room:removed", function (p) {
      var wasCurrent = state.room && state.room.id === p.room_id;
      if (wasCurrent) { state.room = null; showEmpty(); toast(p.reason === "kicked" ? "Bạn đã bị xóa khỏi phòng" : "Phòng đã bị xóa"); }
      loadRooms();
    });
  }
  function resync() {
    if (!state.room) return;
    var id = state.room.id, after = lastMessageId();
    var url = "/api/rooms/" + id + "/messages" + (after ? "?after_id=" + after : "?limit=30");
    api(url).then(function (d) {
      if (!state.room || state.room.id !== id) return;
      if (!after) { state.messages = d.messages; renderMessages(); scrollBottom(); } else d.messages.forEach(addMessage);
      refreshMembers(); loadFiles(); scheduleRead();
    }).catch(function () {});
  }
  function onMessageNew(m) {
    var own = m.sender.id === state.me.id, current = state.room && state.room.id === m.room_id;
    if (current) {
      var added = addMessage(m);
      if (added && m.type === "system") refreshMembers();
      if (added && m.type === "file") loadFiles();
      if (!document.hidden) scheduleRead();
    }
    if (m.type === "system") return;
    if (!own && (!current || document.hidden)) {
      state.rooms.forEach(function (r) { if (r.id === m.room_id) r.unread = (r.unread || 0) + 1; });
      renderRooms();
      var room = state.rooms.filter(function (r) { return r.id === m.room_id; })[0];
      var preview = m.type === "file" ? "📎 " + m.content : m.content.slice(0, 120);
      var title = (room ? room.room_name + " · " : "") + m.sender.full_name;
      toast(title, preview);
      if (document.hidden && "Notification" in window && Notification.permission === "granted") { try { new Notification(title, { body: preview }); } catch (e) {} }
    }
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) scheduleRead(); });

  /* ---------- Hộp thoại: tạo phòng, nhập mã, sửa phòng, tài khoản ---------- */
  function submitting(form, on) { form.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = on; }); }
  $("#btn-create-room").addEventListener("click", function () { $("#form-create").reset(); dlgError($("#form-create")); $("#dlg-create").showModal(); });
  $("#form-create").addEventListener("submit", function (e) {
    e.preventDefault(); var f = e.target; submitting(f, true);
    api("/api/rooms", { method: "POST", json: { name: $("#create-name").value, description: $("#create-desc").value } }).then(function (d) {
      $("#dlg-create").close(); return loadRooms().then(function () { closeDrawers(); openRoom(d.room.id); });
    }).catch(function (err) { dlgError(f, err.message); }).then(function () { submitting(f, false); });
  });
  $("#btn-join-room").addEventListener("click", function () { $("#form-join").reset(); dlgError($("#form-join")); $("#dlg-join").showModal(); });
  function joinWithCode(raw) {
    var code = raw.trim(); var m = code.match(/\/join\/([^/?#\s]+)/); if (m) code = m[1];
    return api("/api/rooms/join", { method: "POST", json: { code: code } }).then(function (d) {
      return loadRooms().then(function () { closeDrawers(); openRoom(d.room.id); toast(d.joined ? "Đã tham gia phòng" : "Bạn đã ở trong phòng này", d.room.room_name); });
    });
  }
  $("#form-join").addEventListener("submit", function (e) {
    e.preventDefault(); var f = e.target; submitting(f, true);
    joinWithCode($("#join-code").value).then(function () { $("#dlg-join").close(); })
      .catch(function (err) { dlgError(f, err.message); }).then(function () { submitting(f, false); });
  });
  $("#btn-edit-room").addEventListener("click", function () {
    $("#edit-name").value = state.room.room_name; $("#edit-desc").value = state.room.description || ""; dlgError($("#form-edit")); $("#dlg-edit").showModal();
  });
  $("#form-edit").addEventListener("submit", function (e) {
    e.preventDefault(); var f = e.target; submitting(f, true);
    api("/api/rooms/" + state.room.id, { method: "PATCH", json: { name: $("#edit-name").value, description: $("#edit-desc").value } })
      .then(function () { $("#dlg-edit").close(); }).catch(function (err) { dlgError(f, err.message); }).then(function () { submitting(f, false); });
  });
  $("#btn-leave-room").addEventListener("click", function () {
    confirmDialog("Rời phòng", "Bạn sẽ không còn xem được tin nhắn của phòng này.", function () {
      api("/api/rooms/" + state.room.id + "/leave", { method: "POST" }).then(function () { state.room = null; showEmpty(); loadRooms(); })
        .catch(function (err) { toast("Lỗi", err.message, true); });
    });
  });
  $("#btn-delete-room").addEventListener("click", function () {
    confirmDialog("Xóa phòng", "Toàn bộ tin nhắn và file của phòng sẽ bị xóa vĩnh viễn.", function () {
      api("/api/rooms/" + state.room.id, { method: "DELETE" }).catch(function (err) { toast("Lỗi", err.message, true); });
    });
  });
  $("#btn-copy-link").addEventListener("click", function () { copyText($("#invite-link").value).then(function () { toast("Đã chép link mời"); }, function () { toast("Không chép được", "Hãy chép thủ công.", true); }); });
  $("#btn-copy-code").addEventListener("click", function () { copyText($("#invite-code").value).then(function () { toast("Đã chép mã phòng"); }, function () { toast("Không chép được", "Hãy chép thủ công.", true); }); });

  function renderMe() {
    setAvatar($("#me-avatar"), state.me); setAvatar($("#set-avatar"), state.me);
    $("#me-name").textContent = state.me.full_name; $("#me-status").textContent = state.me.status_text || "@" + state.me.username;
  }
  $("#btn-settings").addEventListener("click", function () {
    $("#set-name").value = state.me.full_name; $("#set-status").value = state.me.status_text || "";
    $("#set-account").textContent = "@" + state.me.username + " · " + state.me.email; dlgError($("#form-settings")); $("#dlg-settings").showModal();
  });
  $("#form-settings").addEventListener("submit", function (e) {
    e.preventDefault(); var f = e.target; submitting(f, true);
    api("/api/me", { method: "PATCH", json: { full_name: $("#set-name").value, status_text: $("#set-status").value } }).then(function (d) {
      state.me = d.user; renderMe(); $("#dlg-settings").close(); toast("Đã cập nhật hồ sơ");
    }).catch(function (err) { dlgError(f, err.message); }).then(function () { submitting(f, false); });
  });
  $("#btn-avatar").addEventListener("click", function () { $("#avatar-input").click(); });
  $("#avatar-input").addEventListener("change", function (e) {
    var file = e.target.files[0]; e.target.value = ""; if (!file) return;
    var fd = new FormData(); fd.append("file", file);
    fetch("/api/me/avatar", { method: "POST", headers: { "X-CSRFToken": CSRF }, body: fd, credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || "Lỗi " + r.status); return d; }); })
      .then(function (d) { state.me = d.user; renderMe(); toast("Đã đổi ảnh đại diện"); })
      .catch(function (err) { dlgError($("#form-settings"), err.message); });
  });
  $("#logout-form").addEventListener("submit", function () { if (state.socket) state.socket.disconnect(); });

  /* ---------- Tìm kiếm ---------- */
  var searchUsers = debounce(function () {
    var q = $("#side-search").value.trim(), box = $("#user-results");
    if (q.length < 2) { box.hidden = true; return; }
    api("/api/users/search?q=" + encodeURIComponent(q)).then(function (d) {
      var ul = $("#user-results-list"); ul.textContent = ""; box.hidden = false;
      if (!d.users.length) ul.appendChild(el("li", "empty", "Không tìm thấy người dùng."));
      d.users.forEach(function (u) {
        var li = el("li"), b = el("button", "room-item"); b.type = "button";
        var av = el("span", "avatar"); setAvatar(av, u); b.appendChild(av);
        var name = el("span", "name", u.full_name + " (@" + u.username + ")"); b.appendChild(name);
        b.appendChild(el("span", "dot" + (u.online ? " on" : "")));
        b.addEventListener("click", function () {
          api("/api/dm", { method: "POST", json: { user_id: u.id } }).then(function (r) {
            $("#side-search").value = ""; box.hidden = true;
            return loadRooms().then(function () { closeDrawers(); openRoom(r.room.id); });
          }).catch(function (err) { toast("Lỗi", err.message, true); });
        });
        li.appendChild(b); ul.appendChild(li);
      });
    }).catch(function () {});
  }, 300);
  $("#side-search").addEventListener("input", function () { renderRooms(); searchUsers(); });

  $("#btn-room-search").addEventListener("click", function () {
    var p = $("#search-panel"); p.hidden = !p.hidden; if (!p.hidden) $("#room-search-input").focus();
  });
  $("#room-search-input").addEventListener("input", debounce(function () {
    var q = $("#room-search-input").value.trim(), ul = $("#room-search-results"); ul.textContent = "";
    if (!state.room || q.length < 2) return;
    var id = state.room.id;
    api("/api/rooms/" + id + "/messages?limit=30&q=" + encodeURIComponent(q)).then(function (d) {
      if (!state.room || state.room.id !== id) return;
      if (!d.messages.length) ul.appendChild(el("li", null, "Không có tin nhắn phù hợp."));
      d.messages.slice().reverse().forEach(function (m) {
        var li = el("li", null, m.type === "file" ? "📎 " + m.content : m.content);
        li.appendChild(el("small", null, m.sender.full_name + " · " + fmtDay(m.created_at) + " " + fmtTime(m.created_at)));
        ul.appendChild(li);
      });
    }).catch(function (e) { ul.appendChild(el("li", null, e.message)); });
  }, 300));

  /* ---------- Khởi động ---------- */
  api("/api/me").then(function (d) {
    state.me = d.user; renderMe(); connectSocket();
    return loadRooms();
  }).then(function () {
    var code = app.dataset.joinCode;
    if (code) {  // vào bằng link mời /join/<mã>
      history.replaceState(null, "", "/");
      return joinWithCode(code).catch(function (e) { toast("Không tham gia được", e.message, true); });
    }
    var last = null; try { last = parseInt(localStorage.getItem("lastRoom"), 10); } catch (e) {}
    var target = state.rooms.filter(function (r) { return r.id === last; })[0];
    if (target) return openRoom(target.id);
    if (window.matchMedia("(min-width: 768px)").matches && state.rooms.length) return openRoom(state.rooms[0].id);
  }).catch(function (e) { toast("Lỗi tải dữ liệu", e.message, true); });
})();
