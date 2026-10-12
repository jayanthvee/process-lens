/* Vanilla JS for the synthetic messy-crm fixture.
 *
 * The messes this file exists to reproduce, and why each one is here:
 *   - the recipient combobox asks /recipients.json (this origin only) after
 *     every keystroke and rebuilds its options with fresh `:r1:`-style ids,
 *     the way React does. A recorder that grabbed an option by its id records
 *     a selector that dies on the next keystroke. The option's text and its
 *     data-testid are stable, so a correct recorder still has a hold;
 *   - a chip is removed through its own ✕ button, which is nested inside the
 *     chip. The chip is `role="option"` and clickable, so a recorder that
 *     resolves the click by the chip's name records the wrong target: the
 *     ✕'s own `aria-label` ("Remove <address>") has to win;
 *   - the notes box is a `contenteditable`, so the form cannot submit it on
 *     its own. Its text is mirrored into the hidden `notes` input on every
 *     keystroke and again on submit;
 *   - the chips are mirrored into the hidden `recipients` input the same way.
 *     That hidden input is what the fixture stores, and the served markup
 *     pre-fills it with the pre-rendered chip's address, so the save works
 *     before any script runs;
 *   - Save is a plain form POST and a top-level navigation. Nothing here
 *     posts the save itself (no fetch to the save route, no XHR), so the page
 *     after the click is server-rendered;
 *   - the switch effects come from window.__SWITCHES__, which server.py
 *     injects into the page (see the SWITCHES comment token in each page).
 */
(function () {
  "use strict";

  var switches = window.__SWITCHES__ || {};
  var LETTERS = "abcdefghijklmnopqrstuvwxyz";
  var idSeed = 0;
  var suggestTimer = null;

  function byTestId(name) {
    return document.querySelector('[data-testid="' + name + '"]');
  }

  /* Fresh generated ids, React style: the same shape the server mints. */
  function mintId() {
    idSeed += 1;
    return ":r" + idSeed + LETTERS[(idSeed - 1) % LETTERS.length] + ":";
  }

  function closeSuggestions(list, combo) {
    if (!list) {
      return;
    }
    list.hidden = true;
    list.textContent = "";
    if (combo) {
      combo.setAttribute("aria-expanded", "false");
    }
  }

  function renderSuggestions(entries, list, combo) {
    if (!list) {
      return [];
    }
    list.textContent = "";
    entries.forEach(function (entry) {
      var option = document.createElement("div");
      /* A fresh id on every render, and the options are thrown away on the
         next keystroke: this is the whole point of the mess. */
      option.setAttribute("id", mintId());
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", "false");
      option.setAttribute("data-testid", "recipient-suggestion");
      option.setAttribute("data-address", entry.address);
      var name = document.createElement("span");
      name.className = "suggestion-name";
      name.textContent = entry.name;
      var address = document.createElement("span");
      address.className = "suggestion-address";
      address.textContent = entry.address;
      option.appendChild(name);
      option.appendChild(address);
      list.appendChild(option);
    });
    list.hidden = entries.length === 0;
    if (combo) {
      combo.setAttribute("aria-expanded", entries.length ? "true" : "false");
    }
    return entries;
  }

  function askSuggestions(query) {
    return fetch("/recipients.json?q=" + encodeURIComponent(query || ""), {
      headers: { Accept: "application/json" }
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("suggestions failed: " + response.status);
        }
        return response.json();
      })
      .catch(function () {
        /* No answer: leave the listbox closed rather than guess. */
        return { results: [] };
      });
  }

  function addChip(address) {
    var list = byTestId("recipient-chips");
    var value = (address || "").trim();
    if (!list || !value) {
      return null;
    }
    var existing = list.querySelector('[data-recipient="' + value + '"]');
    if (existing) {
      return existing;
    }

    var chip = document.createElement("div");
    chip.className = "chip";
    chip.setAttribute("id", mintId());
    chip.setAttribute("role", "option");
    chip.setAttribute("aria-selected", "false");
    chip.setAttribute("data-testid", "recipient-chip");
    chip.setAttribute("data-recipient", value);

    var name = document.createElement("span");
    name.className = "chip-name";
    name.textContent = value;
    chip.appendChild(name);

    var remove = document.createElement("button");
    remove.setAttribute("type", "button");
    /* The ✕'s own name. It is nested inside the clickable chip on purpose:
       "Remove <address>" has to win over the chip's name. */
    remove.setAttribute("aria-label", "Remove " + value);
    remove.textContent = "✕";
    chip.appendChild(remove);

    list.appendChild(chip);
    syncRecipients();
    return chip;
  }

  function removeChip(chip) {
    if (chip && chip.parentNode) {
      chip.parentNode.removeChild(chip);
    }
    syncRecipients();
  }

  function chipAddresses() {
    var list = byTestId("recipient-chips");
    if (!list) {
      return [];
    }
    return Array.prototype.slice
      .call(list.querySelectorAll("[data-recipient]"))
      .map(function (chip) {
        return chip.getAttribute("data-recipient");
      });
  }

  /* The hidden input is what the form actually posts: a contenteditable and a
     set of chips are invisible to a plain POST on their own. */
  function syncRecipients() {
    var input = document.querySelector('input[name="recipients"]');
    if (input) {
      input.value = chipAddresses().join(",");
    }
  }

  function syncNotes() {
    var box = byTestId("notes-box");
    var input = document.querySelector('input[name="notes"]');
    if (box && input) {
      input.value = String(box.innerText || box.textContent || "");
    }
  }

  function initCombo() {
    var combo = byTestId("recipient-input");
    var list = byTestId("recipient-suggestions");
    var add = byTestId("recipient-add");
    if (!combo || !list || !add) {
      return;
    }

    combo.addEventListener("input", function () {
      window.clearTimeout(suggestTimer);
      suggestTimer = window.setTimeout(function () {
        askSuggestions(combo.value).then(function (payload) {
          renderSuggestions((payload && payload.results) || [], list, combo);
        });
      }, 120);
    });

    combo.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeSuggestions(list, combo);
      }
      if (event.key === "Enter") {
        /* Enter adds what the box holds and must not submit the form: a
           half-typed recipient is not a save. */
        event.preventDefault();
        addChip(combo.value);
        combo.value = "";
        closeSuggestions(list, combo);
      }
    });

    add.addEventListener("click", function () {
      addChip(combo.value);
      combo.value = "";
      closeSuggestions(list, combo);
    });

    list.addEventListener("click", function (event) {
      var node = event.target;
      if (!node || typeof node.closest !== "function") {
        return;
      }
      var option = node.closest('[data-testid="recipient-suggestion"]');
      if (!option) {
        return;
      }
      combo.value = option.getAttribute("data-address") || "";
      closeSuggestions(list, combo);
    });
  }

  function initChips() {
    var list = byTestId("recipient-chips");
    if (!list) {
      return;
    }
    list.addEventListener("click", function (event) {
      var node = event.target;
      if (!node || typeof node.closest !== "function") {
        return;
      }
      var chip = node.closest(".chip");
      if (!chip) {
        return;
      }
      var remove = node.closest("button");
      if (remove && chip.contains(remove)) {
        /* The ✕ wins: its own accessible name is the target, never the
           enclosing chip's. */
        removeChip(chip);
        return;
      }
      /* A plain chip click only selects it; nothing is removed. */
      Array.prototype.forEach.call(list.querySelectorAll(".chip"), function (other) {
        other.setAttribute("aria-selected", other === chip ? "true" : "false");
      });
    });
  }

  function initNotes() {
    var box = byTestId("notes-box");
    if (!box) {
      return;
    }
    box.addEventListener("input", syncNotes);
    box.addEventListener("blur", syncNotes);
    syncNotes();
  }

  function initSaveForm() {
    var form = byTestId("save-form");
    if (!form) {
      return;
    }
    /* A real submit: the browser navigates, so the next page is
       server-rendered. The mirrors run first so the hidden inputs carry the
       contenteditable and the chips. */
    form.addEventListener("submit", function () {
      syncRecipients();
      syncNotes();
      form.setAttribute("aria-busy", "true");
    });
  }

  function initStatus() {
    var onSavedPage = !!(byTestId("saved-summary") || byTestId("nothing-saved"));
    if (!switches.stale_status || onSavedPage) {
      return;
    }
    /* The stale status is byte-identical to a real one, so the markup alone
       cannot prove a save: only /__test__/state can. This attribute is a
       convenience for a test that wants the stale element. The status itself
       is never hidden: hiding it after N ms would race an assertion. */
    Array.prototype.forEach.call(document.querySelectorAll('[role="status"]'), function (node) {
      node.setAttribute("data-stale-status", "true");
    });
  }

  function start() {
    initCombo();
    initChips();
    initNotes();
    initSaveForm();
    initStatus();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  window.messyCrm = {
    addChip: addChip,
    removeChip: removeChip,
    chipAddresses: chipAddresses,
    syncRecipients: syncRecipients,
    syncNotes: syncNotes,
    renderSuggestions: renderSuggestions
  };
})();
