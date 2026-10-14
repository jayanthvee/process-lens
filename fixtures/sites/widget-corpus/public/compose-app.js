// gmail-compose.html: the golden widget, and three traps on purpose.
//
//   * the suggestion listbox is a labelled role="listbox" full of role="option"
//     children -- structurally identical to the chips container beside it;
//   * accepting a suggestion clears the field and adds the chip in the SAME
//     synchronous tick, so a checkpoint that waited for `click` sees only the
//     chip;
//   * the body editor's text is written with a direct `textContent` assignment
//     from the keydown handler, and NO input event is ever dispatched, so a
//     recorder that only listens for `input` cannot see the editor change.
//
// Nothing in this file dispatches an event: the page's own behaviour is the
// fixture.
(function () {
  "use strict";

  var switches = window.Widgets.switches;
  var composeButton = document.querySelector('[data-testid="compose"]');
  var pane = document.querySelector('[data-testid="compose-pane"]');
  var toField = document.querySelector('[data-testid="to"]');
  var listbox = document.getElementById(toField.getAttribute("aria-controls"));
  var chips = document.querySelector('[data-testid="recipient-chips"]');
  var hiddenTo = document.querySelector('input[name="to"]');
  var messageBody = document.querySelector('[data-testid="body-editor"]');
  var bodyInput = document.querySelector('input[name="body"]');
  var sendButton = document.querySelector('[data-testid="send"]');
  var toast = document.querySelector('[data-testid="undo-toast"]');
  var suggests = [];
  var highlighted = -1;
  var bodyText = "";

  // -- the suggestion listbox ------------------------------------------
  function optionText(entry) {
    return entry.name + " <" + entry.address + ">";
  }

  function closeList() {
    suggests = [];
    highlighted = -1;
    listbox.textContent = "";
    listbox.hidden = true;
    toField.setAttribute("aria-expanded", "false");
    toField.removeAttribute("aria-activedescendant");
  }

  function paintList() {
    listbox.textContent = "";
    suggests.forEach(function (entry, index) {
      var option = document.createElement("span");
      option.setAttribute("role", "option");
      option.setAttribute("id", "to-option-" + index);
      option.setAttribute("data-address", entry.address);
      option.setAttribute("data-name", entry.name);
      option.setAttribute("aria-selected", index === highlighted ? "true" : "false");
      option.textContent = optionText(entry);
      listbox.appendChild(option);
    });
    listbox.hidden = suggests.length === 0;
    toField.setAttribute("aria-expanded", suggests.length ? "true" : "false");
    if (highlighted >= 0) {
      toField.setAttribute("aria-activedescendant", "to-option-" + highlighted);
    } else {
      toField.removeAttribute("aria-activedescendant");
    }
  }

  function loadSuggestions(prefix) {
    if (!prefix) {
      closeList();
      return;
    }
    fetch("/directory.json?q=" + encodeURIComponent(prefix))
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        suggests = payload.results || [];
        highlighted = suggests.length ? 0 : -1;
        paintList();
      });
  }

  // -- chips ------------------------------------------------------------
  function mirrorChips() {
    var addresses = [];
    Array.prototype.forEach.call(chips.querySelectorAll(".chip"), function (chip) {
      addresses.push(chip.getAttribute("data-address"));
    });
    hiddenTo.value = addresses.join(",");
    return addresses;
  }

  function addChip(entry) {
    var chip = document.createElement("span");
    chip.className = "chip";
    chip.setAttribute("role", "option");
    chip.setAttribute("data-address", entry.address);
    var name = document.createElement("span");
    name.className = "chip-name";
    name.textContent = entry.name;
    var remove = document.createElement("button");
    remove.setAttribute("type", "button");
    remove.setAttribute("aria-label", "Remove " + entry.address);
    remove.textContent = "\u2715";
    remove.addEventListener("click", function () {
      chip.remove();
      mirrorChips();
    });
    chip.appendChild(name);
    chip.appendChild(remove);
    chips.appendChild(chip);
  }

  // The accept: the chip is added and the field is cleared in one synchronous
  // tick. There is no await between the two lines, so no checkpoint can ever
  // observe one without the other.
  function accept(entry) {
    if (!entry) {
      return;
    }
    addChip(entry);
    toField.value = "";
    closeList();
    mirrorChips();
  }

  // -- the To field -----------------------------------------------------
  toField.addEventListener("input", function () {
    loadSuggestions(toField.value.trim());
  });

  toField.addEventListener("keydown", function (event) {
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && suggests.length) {
      event.preventDefault();
      var step = event.key === "ArrowDown" ? 1 : -1;
      highlighted = (highlighted + step + suggests.length) % suggests.length;
      paintList();
      return;
    }
    if ((event.key === "Enter" || event.key === "Tab") && highlighted >= 0) {
      event.preventDefault();
      accept(suggests[highlighted]);
    }
  });

  listbox.addEventListener("click", function (event) {
    var option = event.target.closest ? event.target.closest('[role="option"]') : null;
    if (!option) {
      return;
    }
    accept({
      name: option.getAttribute("data-name"),
      address: option.getAttribute("data-address")
    });
  });

  // -- the body editor --------------------------------------------------
  // The page owns the editor's text. The keydown handler writes `textContent`
  // itself and mirrors it into the hidden input; the browser's own insertion
  // is prevented. No `input` event is ever dispatched from here, so the only
  // way to see this editor change is to read the DOM.
  messageBody.addEventListener("keydown", function (event) {
    if (event.key === "Backspace") {
      event.preventDefault();
      bodyText = bodyText.slice(0, -1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      bodyText += "\n";
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      bodyText += event.key;
    } else {
      return;
    }
    messageBody.textContent = bodyText;
    bodyInput.value = bodyText;
  });

  // -- Compose, Send and the toast --------------------------------------
  composeButton.addEventListener("click", function () {
    pane.hidden = false;
    document.body.setAttribute("data-compose-open", "true");
    toField.focus();
  });

  sendButton.addEventListener("click", function () {
    var payload = { to: mirrorChips(), body: bodyText, source: "gmail-compose" };
    window.Widgets.postJSON("/send", payload).then(function () {
      if (switches().no_toast) {
        // The switch that proves the assertion is the toast's: the message was
        // stored, and nothing on the page says so.
        return;
      }
      toast.textContent = "Sent \u2014 Undo";
      toast.hidden = false;
      window.setTimeout(function () {
        toast.hidden = true;
      }, 4000);
    });
  });
})();
