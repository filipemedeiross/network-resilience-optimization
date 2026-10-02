(() => {
  "use strict";

  document.querySelectorAll(".instance-generation-form").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector('button[type="submit"]');

      if (!button || button.disabled) {
        return;
      }

      button.disabled    = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = button.dataset.busyLabel || "Preparando instância…";
    });
  });
})();