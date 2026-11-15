// Drag-and-drop reordering of a deck's notes. Progressive enhancement: the up/down buttons
// keep working without it (and on touch screens, which have no HTML5 drag and drop).
//
// Dragging a note's handle moves its row live. When the drag ends with a different order, the
// whole order is POSTed to the server, which answers with the fresh note list. If the server
// refuses (for example the deck changed in another tab), the old order is put back.
(function () {
  "use strict";
  var list = document.getElementById("note-list");
  var errorBox = document.getElementById("note-list-error");
  if (!list || !errorBox || !list.dataset.reorderUrl) return;

  var dragged = null;
  var original = [];
  var busy = false;

  function rows() {
    return Array.prototype.slice.call(list.querySelectorAll("li[data-note-id]"));
  }

  function ids() {
    return rows().map(function (li) {
      return li.dataset.noteId;
    });
  }

  function restore(order) {
    var byId = {};
    rows().forEach(function (li) {
      byId[li.dataset.noteId] = li;
    });
    order.forEach(function (id) {
      if (byId[id]) byId[id].parentNode.appendChild(byId[id]);
    });
  }

  function save(order, previous) {
    busy = true;
    errorBox.textContent = "";
    var body = new URLSearchParams();
    order.forEach(function (id) {
      body.append("order", id);
    });
    fetch(list.dataset.reorderUrl, {
      method: "POST",
      headers: { "HX-Request": "true" },
      body: body,
    })
      .then(function (response) {
        if (response.ok) {
          return response.text().then(function (html) {
            list.innerHTML = html;
            if (window.htmx) window.htmx.process(list);
          });
        }
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            throw new Error(
              typeof data.detail === "string"
                ? data.detail
                : "Could not save the new order (error " + response.status + ")."
            );
          });
      })
      .catch(function (err) {
        restore(previous);
        errorBox.textContent =
          err instanceof TypeError
            ? "Could not reach the server. The order was not saved."
            : err.message;
      })
      .finally(function () {
        busy = false;
      });
  }

  list.addEventListener("dragstart", function (event) {
    var handle = event.target.closest && event.target.closest(".drag-handle");
    if (!handle || busy) {
      event.preventDefault();
      return;
    }
    dragged = handle.closest("li[data-note-id]");
    original = ids();
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", dragged.dataset.noteId); // Firefox needs data
    event.dataTransfer.setDragImage(dragged, 12, 12);
    var row = dragged;
    setTimeout(function () {
      row.classList.add("dragging"); // after the browser has taken the drag image
    }, 0);
  });

  list.addEventListener("dragover", function (event) {
    if (!dragged) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    var target = event.target.closest("li[data-note-id]");
    if (!target || target === dragged) return;
    var box = target.getBoundingClientRect();
    var below = event.clientY > box.top + box.height / 2;
    target.parentNode.insertBefore(dragged, below ? target.nextElementSibling : target);
  });

  list.addEventListener("drop", function (event) {
    if (dragged) event.preventDefault();
  });

  list.addEventListener("dragend", function () {
    if (!dragged) return;
    dragged.classList.remove("dragging");
    dragged = null;
    var order = ids();
    if (order.join(",") !== original.join(",")) save(order, original);
  });
})();
