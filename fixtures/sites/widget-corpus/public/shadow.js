// shadow-form.html: the form lives inside an OPEN shadow root, so a recorder
// has to descend through `element.shadowRoot` to find it. The host element in
// the light DOM is empty; everything below is the shadow tree.
(function () {
  "use strict";

  var host = document.querySelector('[data-testid="shadow-host"]');
  var root = host.attachShadow({ mode: "open" });

  root.innerHTML = [
    '<label for="shadow-name">Name</label>',
    '<input id="shadow-name" data-testid="shadow-name" name="name" type="text"',
    ' aria-label="Name">',
    '<label for="shadow-team">Team</label>',
    '<select id="shadow-team" data-testid="shadow-team" name="team" aria-label="Team">',
    '<option value="sales">Sales</option>',
    '<option value="support">Support</option>',
    '<option value="success">Customer success</option>',
    "</select>",
    '<button type="button" data-testid="shadow-save">Save</button>',
    '<div role="status" data-testid="shadow-saved" hidden>Saved</div>'
  ].join("");

  var name = root.querySelector('[data-testid="shadow-name"]');
  var team = root.querySelector('[data-testid="shadow-team"]');
  var saveButton = root.querySelector('[data-testid="shadow-save"]');
  var saved = root.querySelector('[data-testid="shadow-saved"]');

  saveButton.addEventListener("click", function () {
    window.Widgets.save("shadow-form", { name: name.value, team: team.value }).then(function () {
      saved.hidden = false;
    });
  });
})();
