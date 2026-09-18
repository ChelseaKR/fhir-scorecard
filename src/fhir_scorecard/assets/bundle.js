// /bundle/: announce two steps toward a purchase (docs/adr/0007-bundle-conversion-events.md).
//
// The page ships this script only when a plan is on sale (site.bundle_offers). It announces
// that the plans were shown (view_item) and that a checkout link was followed (begin_checkout)
// as a "fhir-scorecard:commerce" event on the document. It never calls Google itself: the
// analytics loader in <head> (analytics.py) is the only code that does, it forwards an event
// only when GA4 loaded at all (so never off the production host, under Global Privacy Control
// or Do Not Track, or after the footer opt-out), and it rebuilds every parameter from fields it
// checks one at a time. What this script puts in an event is a plan id and a price read from
// the page's own markup, both of which came from data/bundle/plan.json, and nothing about the
// reader.

(function () {
  "use strict";
  var box = document.getElementById("bundle-offers");
  if (!box) {
    return;
  }
  var currency = box.getAttribute("data-currency") || "";
  var links = box.querySelectorAll("a[data-bundle-plan]");

  function announce(event, items) {
    if (!items.length) {
      return;
    }
    document.dispatchEvent(
      new CustomEvent("fhir-scorecard:commerce", {
        detail: { event: event, currency: currency, value: items[0].price, items: items },
      }),
    );
  }

  var shown = [];
  for (var i = 0; i < links.length; i++) {
    var link = links[i];
    var item = {
      item_id: link.getAttribute("data-bundle-plan") || "",
      price: Number(link.getAttribute("data-bundle-price")),
    };
    shown.push(item);
    link.addEventListener(
      "click",
      (function (chosen) {
        return function () {
          announce("begin_checkout", [chosen]);
        };
      })(item),
    );
  }
  // Valued at the first plan shown, the entry bundle, the way gtfs-scorecard values its own.
  announce("view_item", shown);
})();
