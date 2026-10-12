/* Vanilla JS for the synthetic lead-crm fixture.
 *
 * No framework, no build step, and no request leaves this origin:
 *   - the search fetch talks to /search.json on the CRM origin only;
 *   - the form submit handler deliberately does NOT prevent the default for a
 *     non-empty query, so the click lands on a settled, server-rendered page
 *     (the same markup the fetch renders). /search.json stays a pure
 *     enhancement, used for the live search while typing;
 *   - the toast is annotated, never auto-hidden: hiding it after N ms would
 *     race an assertion that looks for "Lead created";
 *   - the switch effects come from window.__SWITCHES__, which server.py
 *     injects into the page (see the <!--SWITCHES--> token).
 */
(function () {
  "use strict";

  var switches = window.__SWITCHES__ || {};

  function byId(id) {
    return document.getElementById(id);
  }

  function emptyState() {
    var p = document.createElement("p");
    p.className = "empty";
    p.setAttribute("data-testid", "no-results");
    p.textContent = "No leads found";
    return p;
  }

  function cell(text) {
    var div = document.createElement("div");
    div.setAttribute("role", "cell");
    div.className = "result-cell";
    div.textContent = text == null ? "" : String(text);
    return div;
  }

  function renderResults(payload) {
    var section = byId("results-section");
    if (!section) {
      return;
    }
    var results = (payload && payload.results) || [];
    section.textContent = "";
    if (results.length === 0) {
      section.appendChild(emptyState());
      return;
    }

    var table = document.createElement("div");
    table.setAttribute("role", "table");
    table.setAttribute("aria-label", "Search results");
    table.className = "results";
    table.setAttribute("data-testid", "lead-results");

    var head = document.createElement("div");
    head.setAttribute("role", "rowgroup");
    head.className = "results-head";
    var headRow = document.createElement("div");
    headRow.setAttribute("role", "row");
    headRow.className = "result-row";
    ["Email", "Interest", "Company"].forEach(function (name) {
      var th = document.createElement("div");
      th.setAttribute("role", "columnheader");
      th.className = "result-cell";
      th.textContent = name;
      headRow.appendChild(th);
    });
    head.appendChild(headRow);

    var body = document.createElement("div");
    body.setAttribute("role", "rowgroup");
    body.className = "results-body";
    results.forEach(function (lead) {
      var row = document.createElement("div");
      row.setAttribute("role", "row");
      row.className = "result-row";
      row.setAttribute("data-lead-row", "");
      row.appendChild(cell(lead.email));
      row.appendChild(cell(lead.interest));
      row.appendChild(cell(lead.company));
      body.appendChild(row);
    });

    table.appendChild(head);
    table.appendChild(body);
    section.appendChild(table);
  }

  /* The search fetch: same origin, JSON, no navigation. */
  function runSearch(value) {
    var url = "/search.json?q=" + encodeURIComponent(value || "");
    return fetch(url, { headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("search failed: " + response.status);
        }
        return response.json();
      })
      .then(function (payload) {
        renderResults(payload);
        return payload;
      })
      .catch(function () {
        /* Leave the server-rendered results in place. */
        return null;
      });
  }

  function initSearch() {
    var form = byId("search-form");
    var input = byId("lead-search");
    if (!form || !input) {
      return;
    }

    var timer = null;
    input.addEventListener("input", function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        runSearch(input.value);
      }, 150);
    });

    form.addEventListener("submit", function (event) {
      var value = (input.value || "").trim();
      if (!value) {
        /* An empty query has nothing to ask the server for. */
        event.preventDefault();
        renderResults({ results: [] });
        return;
      }
      /* Non-empty query: navigate. The server renders the results table, which
         is exactly what renderResults() would build. */
      form.setAttribute("aria-busy", "true");
    });
  }

  function initNewLead() {
    var form = byId("new-lead-form");
    if (!form) {
      return;
    }
    var hint = byId("save-hint");
    var frame = byId("lead-email-frame");
    var bridge = byId("lead-email-bridge");

    if (frame && bridge) {
      /* field_in_iframe switch: the Email control lives in a same-origin
         srcdoc iframe. A form cannot post a control from another document,
         so mirror the value into the hidden bridge input. */
      var mirror = function () {
        try {
          var doc = frame.contentDocument;
          var inner = doc && doc.getElementById("lead-email");
          if (inner) {
            bridge.value = inner.value;
          }
        } catch (err) {
          /* The bridge stays empty; the server rejects that save. */
        }
      };
      frame.addEventListener("load", function () {
        try {
          var doc = frame.contentDocument;
          var inner = doc && doc.getElementById("lead-email");
          if (inner) {
            inner.addEventListener("input", mirror);
            inner.addEventListener("change", mirror);
          }
        } catch (err) {
          /* Nothing to mirror. */
        }
      });
      form.addEventListener("submit", mirror);
    }

    form.addEventListener("submit", function () {
      if (hint && switches.slow_save_ms) {
        hint.hidden = false;
        hint.textContent = "Saving, this can take a moment...";
      }
      form.setAttribute("aria-busy", "true");
    });
  }

  function initToast() {
    var toast = document.querySelector('[role="status"]');
    if (!toast) {
      return;
    }
    if (switches.stale_toast) {
      /* The leftover toast is byte-identical to a real one, so nothing in the
         markup can tell them apart. This attribute is a convenience for a
         test that wants the stale element; /__test__/state stays the only
         authority on whether a save happened. The toast itself is never
         hidden: hiding it after N ms would race an assertion. */
      toast.setAttribute("data-stale-toast", "true");
    }
  }

  function start() {
    initSearch();
    initNewLead();
    initToast();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  window.leadCrm = { renderResults: renderResults, runSearch: runSearch };
})();
