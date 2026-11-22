/* Funera website: the only script. No framework, no inline code (the Content-Security-Policy forbids it). */
(function () {
  "use strict";

  // Mobile menu
  var toggle = document.querySelector("[data-menu-toggle]");
  var menu = document.querySelector("[data-menu]");
  if (toggle && menu) {
    var setOpen = function (open) {
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      menu.classList.toggle("hidden", !open);
    };
    toggle.addEventListener("click", function () { setOpen(toggle.getAttribute("aria-expanded") !== "true"); });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") { setOpen(false); toggle.focus(); }
    });
    menu.addEventListener("click", function (event) { if (event.target.closest("a")) { setOpen(false); } });
  }

  // Contact form: stop double submits and say we are working.
  var form = document.querySelector("[data-lead-form]");
  if (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector("button[type=submit]");
      if (button) { button.disabled = true; button.textContent = "Sending…"; }
    });
  }
})();
