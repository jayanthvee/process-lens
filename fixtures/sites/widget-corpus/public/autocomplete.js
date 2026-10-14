// react-autocomplete.html: a widget that accepts a suggestion only by CLICKING
// the option. Enter and Tab are explicitly inert, so a recording that accepts
// this widget with a key is a recording of something that does not happen.
//
// The chip appears and the field clears in the same synchronous tick, and the
// Save button persists the accepted address. Nothing here dispatches an event.
(function () {
  "use strict";

  var field = document.querySelector('[data-testid="reviewer"]');
  var listbox = document.getElementById(field.getAttribute("aria-controls"));
  var chips = document.querySelector('[data-testid="reviewer-chips"]');
  var hidden = document.querySelector('input[name="reviewer"]');
  var saveButton = document.querySelector('[data-testid="save"]');
  var saved = document.querySelector('[data-testid="saved"]');
  var suggests = [];

  function optionText(entry) {
    return entry.name + " <" + entry.address + ">";
  }

  function closeList() {
    suggests = [];
    listbox.textContent = "";
    listbox.hidden = true;
    field.setAttribute("aria-expanded", "false");
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
        listbox.textContent = "";
        suggests.forEach(function (entry, index) {
          var option = document.createElement("span");
          option.setAttribute("role", "option");
          option.setAttribute("id", "reviewer-option-" + index);
          option.setAttribute("data-address", entry.address);
          option.setAttribute("data-name", entry.name);
          option.textContent = optionText(entry);
          listbox.appendChild(option);
        });
        listbox.hidden = suggests.length === 0;
        field.setAttribute("aria-expanded", suggests.length ? "true" : "false");
      });
  }

  function mirror() {
    var addresses = [];
    Array.prototype.forEach.call(chips.querySelectorAll(".chip"), function (chip) {
      addresses.push(chip.getAttribute("data-address"));
    });
    hidden.value = addresses.join(",");
    return addresses;
  }

  function accept(entry) {
    if (!entry) {
      return;
    }
    // The chip and the cleared field, in one synchronous tick.
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
      mirror();
    });
    chip.appendChild(name);
    chip.appendChild(remove);
    chips.appendChild(chip);
    field.value = "";
    closeList();
    mirror();
  }

  field.addEventListener("input", function () {
    loadSuggestions(field.value.trim());
  });

  // Clicking is the only way to accept. Enter and Tab do nothing at all.
  field.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
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

  saveButton.addEventListener("click", function () {
    var addresses = mirror();
    window.Widgets.save("react-autocomplete", { reviewer: addresses.join(",") }).then(function () {
      window.Widgets.reveal(saved);
    });
  });
})();
