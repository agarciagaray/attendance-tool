// Opens the memo editor in place of following the link to the memo page.
// The text is always set with textContent or as a field value, never as HTML.
(function () {
  var dialog = document.getElementById("memo-dialog");
  if (!dialog || !dialog.showModal) return;
  var form = dialog.querySelector("form");
  var text = dialog.querySelector("textarea");
  var title = dialog.querySelector('[data-memo-slot="title"]');

  document.addEventListener("click", function (event) {
    var link = event.target.closest("[data-memo-url]");
    if (!link) return;
    event.preventDefault();
    form.action = link.dataset.memoUrl;
    title.textContent = link.dataset.memoTitle;
    text.value = link.dataset.memo;
    dialog.showModal();
    text.focus();
  });

  dialog.querySelector("[data-memo-cancel]").addEventListener("click", function () {
    dialog.close();
  });
})();
