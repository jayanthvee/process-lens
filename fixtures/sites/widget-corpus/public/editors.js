// plain-editor.html and framework-editor.html share this file; the page's
// <body data-mode> picks the behaviour.
//
//   * "plain": the browser fires `input` while the person types and the page
//     mirrors the editor's text into the hidden input on every one of them.
//     This is the baseline that must not regress.
//   * "framework": the page owns the text. A keydown handler writes
//     `textContent` directly and a timer re-renders it, so the visible text is
//     script's and NO input event is ever dispatched. A recorder that only
//     listens for `input` sees nothing at all here; the snapshot is the only
//     source.
(function () {
  "use strict";

  var mode = document.body.getAttribute("data-mode") || "plain";
  var source = document.body.getAttribute("data-source") || "plain-editor";
  var field = document.body.getAttribute("data-field") || "note";
  var editor = document.querySelector('[data-testid="editor"]');
  var mirror = document.querySelector('input[name="note"]');
  var saveButton = document.querySelector('[data-testid="save"]');
  var saved = document.querySelector('[data-testid="saved"]');
  var model = "";

  function store(text) {
    mirror.value = text;
  }

  function save() {
    var text = mode === "framework" ? model : editor.textContent || "";
    var fields = {};
    fields[field] = text;
    window.Widgets.save(source, fields).then(function () {
      window.Widgets.reveal(saved);
    });
  }

  if (mode === "framework") {
    editor.addEventListener("keydown", function (event) {
      if (event.key === "Backspace") {
        event.preventDefault();
        model = model.slice(0, -1);
      } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        model += event.key;
      } else {
        return;
      }
      editor.textContent = model;
      store(model);
    });
    // A framework re-renders from its own state: the page writes the visible
    // text again on a timer, and never dispatches an input event.
    window.setInterval(function () {
      if (editor.textContent !== model) {
        editor.textContent = model;
      }
    }, 500);
  } else {
    editor.addEventListener("input", function () {
      store(editor.textContent || "");
    });
  }

  saveButton.addEventListener("click", save);
})();
