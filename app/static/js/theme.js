// Áp dụng Light/Dark Mode trước khi trang vẽ để tránh nháy màu. Chỉ lưu lựa chọn giao diện, KHÔNG lưu dữ liệu chat.
(function () {
  var saved = null;
  try { saved = localStorage.getItem("theme"); } catch (e) {}
  var dark = saved ? saved === "dark" : window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  window.toggleTheme = function () {
    var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) {}
    return next;
  };
})();
