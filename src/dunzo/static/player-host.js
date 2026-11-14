// Hosts one player at a time in a sandboxed iframe.
//
// The iframe gets sandbox="allow-scripts" and nothing more (in particular not the same-origin
// permission), so the player runs in an opaque origin with no access to this page, its cookies or storage. The only channel is
// postMessage, and only messages from the current iframe's window are accepted.
var PlayerHost = (function () {
  "use strict";

  function create(container, handlers) {
    var frame = null;
    var card = null;

    function onMessage(event) {
      if (!frame || event.source !== frame.contentWindow) return;
      var message = event.data;
      if (!message || typeof message !== "object") return;
      if (message.type === "ready") {
        frame.contentWindow.postMessage({ type: "init", card: card }, "*");
      } else if (message.type === "done") {
        handlers.onDone(message.grade, message.detail === undefined ? null : message.detail);
      } else if (message.type === "error") {
        handlers.onError(String(message.message || "Unknown player error"));
      }
    }
    window.addEventListener("message", onMessage);

    function destroy() {
      if (frame) frame.remove();
      frame = null;
      card = null;
    }

    return {
      load: function (frameUrl, cardData) {
        destroy();
        card = cardData;
        frame = document.createElement("iframe");
        frame.setAttribute("sandbox", "allow-scripts");
        frame.className = "player-frame";
        frame.title = "Card";
        frame.src = frameUrl;
        container.appendChild(frame);
      },
      destroy: destroy,
    };
  }

  return { create: create };
})();
