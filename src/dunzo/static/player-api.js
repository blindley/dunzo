// The API a player sees inside its sandboxed iframe (window.dunzo). Host code: it is loaded
// before the player's own script and talks to the host page only through postMessage.
//
//   dunzo.onInit(function (card) { ... })   card = { note: { id, properties, media }, index, card_count }
//                                            note.media = [{ id, mime, size, url }]
//   dunzo.done(grade, detail)               grade 1-4 (1=Again, 2=Hard, 3=Good, 4=Easy); detail optional JSON
//   dunzo.mediaUrl(hash)                    host-served URL for a listed media hash, or null
//
// Messages: host -> player {type: "init", card}; player -> host {type: "ready"},
// {type: "done", grade, detail}, {type: "error", message}.
(function () {
  "use strict";
  var callbacks = [];
  var card = null;

  function post(message) {
    parent.postMessage(message, "*"); // the frame has an opaque origin: "*" is the only target
  }

  window.dunzo = {
    onInit: function (callback) {
      if (typeof callback !== "function") throw new TypeError("dunzo.onInit needs a function");
      callbacks.push(callback);
      if (card) callback(card);
    },
    done: function (grade, detail) {
      try {
        post({ type: "done", grade: grade, detail: detail });
      } catch (err) {
        post({ type: "error", message: "Could not send the report: " + err.message });
      }
    },
    mediaUrl: function (hash) {
      var list = (card && card.note && card.note.media) || [];
      for (var i = 0; i < list.length; i++) {
        if (list[i].id === hash) return list[i].url;
      }
      return null;
    },
  };

  window.addEventListener("message", function (event) {
    if (event.source !== parent) return;
    var message = event.data;
    if (!message || message.type !== "init" || !message.card) return;
    card = message.card;
    callbacks.slice().forEach(function (callback) {
      callback(card);
    });
  });

  window.addEventListener("error", function (event) {
    post({ type: "error", message: String(event.message) });
  });

  // Scripts run before DOMContentLoaded, so the player has registered its callbacks by now.
  document.addEventListener("DOMContentLoaded", function () {
    post({ type: "ready" });
  });
})();
