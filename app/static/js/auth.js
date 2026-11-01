// Hiện/ẩn mật khẩu và kiểm tra nhanh phía client (server vẫn kiểm tra lại toàn bộ).
(function () {
  document.querySelectorAll("[data-toggle-password]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var ids = btn.getAttribute("data-toggle-password").split(",");
      var show = null;
      ids.forEach(function (id) {
        var input = document.getElementById(id);
        if (!input) return;
        if (show === null) show = input.type === "password";
        input.type = show ? "text" : "password";
      });
      if (btn.classList.contains("inline")) return;
      btn.textContent = show ? "Ẩn" : "Hiện";
    });
  });

  var form = document.getElementById("register-form");
  if (!form) return;
  function setError(name, msg) {
    var box = form.querySelector('[data-error-for="' + name + '"]');
    var input = form.elements[name];
    if (box) box.textContent = msg || "";
    if (input) input.classList.toggle("invalid", !!msg);
  }
  form.addEventListener("submit", function (e) {
    var f = form.elements, ok = true;
    var rules = {
      full_name: [f.full_name.value.trim().length >= 2, "Nhập họ tên (ít nhất 2 ký tự)."],
      username: [/^[A-Za-z0-9_.]{3,32}$/.test(f.username.value.trim()), "Username 3-32 ký tự: chữ, số, dấu chấm, gạch dưới."],
      email: [/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email.value.trim()), "Email không hợp lệ."],
      password: [f.password.value.length >= 8 && /[A-Za-z]/.test(f.password.value) && /\d/.test(f.password.value), "Mật khẩu tối thiểu 8 ký tự, gồm chữ và số."],
      confirm: [f.confirm.value === f.password.value && f.confirm.value !== "", "Mật khẩu nhập lại không khớp."]
    };
    Object.keys(rules).forEach(function (k) {
      var bad = !rules[k][0];
      setError(k, bad ? rules[k][1] : "");
      if (bad) ok = false;
    });
    if (!ok) e.preventDefault();
  });
})();
