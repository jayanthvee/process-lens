// Shared plumbing for every widget-corpus page.
//
// Nothing here dispatches an event: the pages are the fixture, and a synthetic
// event would be a fixture bug rather than a page's behaviour.
(function () {
  "use strict";

  function switches() {
    return window.__SWITCHES__ || {};
  }

  function postJSON(path, payload) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (response) {
      if (!response.ok) {
        throw new Error(path + " answered " + response.status);
      }
      return response.json();
    });
  }

  function save(source, fields) {
    return postJSON("/save", { source: source, fields: fields });
  }

  function reveal(element) {
    if (element) {
      element.hidden = false;
    }
  }

  window.Widgets = {
    switches: switches,
    postJSON: postJSON,
    save: save,
    reveal: reveal
  };
})();
