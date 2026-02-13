(function () {
  const html = document.documentElement;
  const themeToggle = document.getElementById("themeToggle");
  const stored = localStorage.getItem("snapurl-theme");
  if (stored === "dark" || stored === "light") {
    html.setAttribute("data-theme", stored);
  }

  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      const current = html.getAttribute("data-theme") || "light";
      const next = current === "light" ? "dark" : "light";
      html.setAttribute("data-theme", next);
      localStorage.setItem("snapurl-theme", next);
    });
  }

  // Sync visible options checkboxes into submitted form
  const form = document.querySelector("form.capture-form");
  if (form) {
    form.addEventListener("submit", () => {
      const optionContainer = document.querySelector(".options");
      if (!optionContainer) return;

      // Remove old hidden fields
      form.querySelectorAll("input[data-synced='1']").forEach(el => el.remove());

      const checkboxes = optionContainer.querySelectorAll("input[type='checkbox']");
      checkboxes.forEach((cb) => {
        if (cb.checked) {
          const hidden = document.createElement("input");
          hidden.type = "hidden";
          hidden.name = cb.name;
          hidden.value = "on";
          hidden.setAttribute("data-synced", "1");
          form.appendChild(hidden);
        }
      });
    });
  }
})();