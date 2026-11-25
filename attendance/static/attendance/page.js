// Small behaviours shared by the pages: tabs, "show all" on long tables,
// the day picker under the month calendar, and opening folded days for print.
// Every page still works without this file; it only shows less at once.
(function () {
  function recall(key) {
    try { return sessionStorage.getItem(key); } catch (error) { return null; }
  }

  function remember(key, value) {
    try { sessionStorage.setItem(key, value); } catch (error) {}
  }

  // Tabs: one panel at a time. The choice is kept for the visit, so moving to
  // another month or year stays on the same tab.
  document.querySelectorAll("[data-tabs]").forEach(function (group) {
    var tabs = Array.prototype.slice.call(group.querySelectorAll('[role="tab"]'));
    var panels = group.querySelectorAll("[data-panel]");
    var key = "tab:" + group.dataset.tabs;
    var names = tabs.map(function (tab) { return tab.dataset.tab; });

    function choose(name) {
      tabs.forEach(function (tab) {
        var on = tab.dataset.tab === name;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.tabIndex = on ? 0 : -1;
      });
      panels.forEach(function (panel) {
        panel.classList.toggle("tab-hidden", panel.dataset.panel !== name);
      });
    }

    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () {
        choose(tab.dataset.tab);
        remember(key, tab.dataset.tab);
      });
      tab.addEventListener("keydown", function (event) {
        var step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        var next = tabs[(index + step + tabs.length) % tabs.length];
        next.focus();
        next.click();
      });
    });

    var first = [group.dataset.open, recall(key), names[0]].filter(function (name) {
      return names.indexOf(name) !== -1;
    })[0];
    group.querySelector('[role="tablist"]').hidden = false;
    group.classList.add("tabs-on");
    choose(first);
  });

  // Long tables show their first ten rows until asked for the rest.
  document.querySelectorAll("[data-show-all]").forEach(function (button) {
    button.addEventListener("click", function () {
      document.getElementById(button.dataset.showAll).classList.remove("rows-capped");
      button.remove();
    });
  });

  // The month calendar: pressing a day shows that day's sessions underneath.
  document.querySelectorAll("[data-day-panel]").forEach(function (panel) {
    var cells = document.querySelectorAll("[data-day]");
    var hint = panel.querySelector("[data-day-hint]");

    function show(cell, scroll) {
      cells.forEach(function (other) {
        other.setAttribute("aria-pressed", other === cell ? "true" : "false");
      });
      panel.querySelectorAll("[data-day-detail]").forEach(function (detail) {
        detail.hidden = detail.dataset.dayDetail !== cell.dataset.day;
      });
      hint.hidden = true;
      if (scroll) panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    cells.forEach(function (cell) {
      cell.addEventListener("click", function () { show(cell, true); });
      if (cell.hasAttribute("data-today")) show(cell, false);
    });
    panel.hidden = false;
  });

  // The record lists one line per day; each opens to its sessions.
  document.querySelectorAll("[data-open-days]").forEach(function (button) {
    button.addEventListener("click", function () {
      var days = document.querySelectorAll("#" + button.dataset.openDays + " details");
      var open = button.getAttribute("aria-pressed") !== "true";
      days.forEach(function (day) { day.open = open; });
      button.setAttribute("aria-pressed", open ? "true" : "false");
      button.textContent = open ? "Cerrar todos los días" : "Abrir todos los días";
    });
  });

  // "Are you still working?" The server decides when a timer stops; this only
  // puts the question in front of the employee and counts down to that moment.
  var question = document.getElementById("still-working");
  if (question && question.showModal) {
    var offset = Number(question.dataset.now) - Date.now();
    var askAt = Number(question.dataset.ask);
    var stopAt = Number(question.dataset.stop);
    var countdown = question.querySelector("[data-countdown]");
    question.addEventListener("cancel", function (event) { event.preventDefault(); });  // Esc is not an answer

    var watch = setInterval(function () {
      var now = Date.now() + offset;
      if (now < askAt) return;
      if (now >= stopAt + 1500) {
        clearInterval(watch);
        location.href = question.dataset.home;  // the timer has been stopped; the clock page says so
        return;
      }
      var left = Math.max(0, Math.ceil((stopAt - now) / 1000));
      countdown.textContent = Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0");
      if (!question.open) {
        question.showModal();
        document.title = "¿Sigues trabajando?";
      }
    }, 1000);
  }

  // Paper gets everything: folded days are opened for printing, then put back.
  var reopened = [];
  window.addEventListener("beforeprint", function () {
    reopened = Array.prototype.slice.call(document.querySelectorAll("details:not([open])"));
    reopened.forEach(function (item) { item.open = true; });
  });
  window.addEventListener("afterprint", function () {
    reopened.forEach(function (item) { item.open = false; });
  });
})();
