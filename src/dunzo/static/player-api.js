// The API a player sees inside its sandboxed iframe (window.dunzo). Host code: the server
// hands it to every player as dunzo/api.js. A player's index.html loads it, then
// dunzo/card.js (which sets window.dunzoCard to the card being reviewed), then its own code:
//
//   <script src="dunzo/api.js"></script>
//   <script src="dunzo/card.js"></script>
//
//   dunzo.onInit(function (card) { ... })   card = { note: { id, properties, media }, index, card_count }
//                                            note.media = [{ id, mime, size, url }]
//                                            Runs once the page has loaded (immediately if it already has).
//   dunzo.card                              the same card object, available once dunzo/card.js has run
//   dunzo.done(grade, detail)               grade 1-4 (1=Again, 2=Hard, 3=Good, 4=Easy); detail optional JSON
//   dunzo.mediaUrl(hash)                    host-served URL for a listed media hash, or null
//
// Messages to the host (the only channel out of the frame): {type: "done", grade, detail}
// and {type: "error", message}.
(function () {
  "use strict";
  var callbacks = [];
  var started = false;

  function post(message) {
    parent.postMessage(message, "*"); // the frame has an opaque origin: "*" is the only target
  }

  function currentCard() {
    return window.dunzoCard || null;
  }

  function run(callback) {
    try {
      callback(currentCard());
    } catch (err) {
      post({ type: "error", message: String((err && err.message) || err) });
    }
  }

  window.dunzo = {
    onInit: function (callback) {
      if (typeof callback !== "function") throw new TypeError("dunzo.onInit needs a function");
      if (started) run(callback);
      else callbacks.push(callback);
    },
    done: function (grade, detail) {
      try {
        post({ type: "done", grade: grade, detail: detail });
      } catch (err) {
        post({ type: "error", message: "Could not send the report: " + err.message });
      }
    },
    mediaUrl: function (hash) {
      var card = currentCard();
      var list = (card && card.note && card.note.media) || [];
      for (var i = 0; i < list.length; i++) {
        if (list[i].id === hash) return list[i].url;
      }
      return null;
    },
  };
  Object.defineProperty(window.dunzo, "card", { get: currentCard, enumerable: true });

  window.addEventListener("error", function (event) {
    post({ type: "error", message: String(event.message) });
  });

  function start() {
    if (!currentCard()) {
      post({
        type: "error",
        message: 'dunzo/card.js was not loaded. Add <script src="dunzo/card.js"></script> after dunzo/api.js in index.html.',
      });
      return;
    }
    started = true;
    callbacks.splice(0).forEach(run);
  }

  // Scripts run in order before DOMContentLoaded, so card.js has run by now.
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
