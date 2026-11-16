// Review shell: asks the server for the next card, shows it in a player, and sends the
// player's report back. The server decides which card comes next.
(function () {
  "use strict";
  var root = document.getElementById("review");
  if (!root) return;
  var stage = document.getElementById("review-stage");
  var counts = document.getElementById("review-counts");
  var errorBox = document.getElementById("review-error");
  var current = null;
  var busy = false;

  var host = PlayerHost.create(stage, { onDone: onDone, onError: showError });

  function showError(message) {
    errorBox.textContent = message;
  }

  function request(url, options) {
    return fetch(url, options).then(function (response) {
      if (response.ok) return response.json();
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (body) {
          throw new Error(typeof body.detail === "string" ? body.detail : "Request failed (" + response.status + ")");
        });
    });
  }

  function render(data) {
    errorBox.textContent = "";
    counts.textContent = data.counts.due + " due · " + data.counts.new + " new";
    if (data.done) {
      host.destroy();
      current = null;
      stage.textContent = "";
      var message = document.createElement("p");
      message.className = "empty";
      message.textContent = "Nothing left to review right now. ";
      var back = document.createElement("a");
      back.href = root.dataset.deckUrl;
      back.textContent = "Back to the deck";
      message.appendChild(back);
      stage.appendChild(message);
      return;
    }
    current = { note_id: data.card.note.id, card_index: data.card.index };
    host.load(data.frame_url);
  }

  function onDone(grade, detail) {
    if (busy || !current) return;
    busy = true;
    var payload = { note_id: current.note_id, card_index: current.card_index, grade: grade };
    if (detail !== null && detail !== undefined) payload.detail = detail;
    request(root.dataset.reportUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(render)
      .catch(function (err) {
        showError(err.message);
      })
      .finally(function () {
        busy = false;
      });
  }

  request(root.dataset.nextUrl).then(render).catch(function (err) {
    showError(err.message);
  });
})();
