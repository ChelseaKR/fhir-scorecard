// Post-checkout setup form (/bundle/setup/). Stripe redirects here with ?session_id=... after
// a successful Checkout. The form POSTs the organization details plus that session id to the
// compliance-bundle API (window.FHIR_SCORECARD_BUNDLE_URL), which confirms the payment with
// Stripe before anything is built. Until that endpoint is deployed, or if the page is reached
// without a session id, the form is disabled and says why -- see
// docs/compliance-bundle-plan.md for what deploying it requires.

(function () {
  "use strict";
  var form = document.getElementById("bundle-setup-form");
  var status = document.getElementById("bundle-setup-status");
  var endpoint = window.FHIR_SCORECARD_BUNDLE_URL;
  var sessionId = new URLSearchParams(location.search).get("session_id") || "";

  function setStatus(message, kind) {
    if (!status) return;
    status.textContent = message;
    status.className = "form-status form-status-" + kind;
  }

  function enable(on) {
    if (!form) return;
    var elements = form.elements;
    for (var i = 0; i < elements.length; i++) {
      elements[i].disabled = !on;
    }
  }

  if (!form) {
    return;
  }
  if (!endpoint) {
    enable(false);
    setStatus(
      sessionId
        ? "Your payment went through, but the setup service is not deployed yet, so this form " +
            "cannot submit. Nothing is lost. Keep the full web address of this page, which " +
            "carries your order reference, and open an issue at " +
            "github.com/ChelseaKR/fhir-scorecard/issues, and the bundle will be set up by hand."
        : "The setup service is not deployed yet, so this form cannot submit. Nothing has been " +
            "charged.",
      "info",
    );
    return;
  }
  if (!/^cs_[A-Za-z0-9_]+$/.test(sessionId)) {
    enable(false);
    setStatus(
      "This page needs the order reference Stripe adds to its address after checkout, and " +
        "this address does not carry one. If you have already paid and lost that page, open " +
        "an issue at github.com/ChelseaKR/fhir-scorecard/issues and the bundle will be set up " +
        "by hand. Do not pay again.",
      "info",
    );
    return;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var data = Object.fromEntries(new FormData(form).entries());
    var ids = String(data.endpoint_ids || "").trim();
    if (!String(data.program_name || "").trim() || !ids) {
      setStatus("Please give the organization name and at least one endpoint id.", "err");
      return;
    }
    // A ceiling across every plan, not a promise about the plan this buyer paid for. The page
    // does not know which price was paid; the server does, and answers a list over that cap
    // with the limit of the plan bought before it consumes the checkout, so the buyer can trim
    // and resend. This number must equal MAX_ENDPOINTS (fhir_scorecard/bundle.py).
    if (ids.split(/[\s,]+/).filter(Boolean).length > 70) {
      setStatus("No bundle covers more than 70 endpoints. Trim the list and send it again.", "err");
      return;
    }
    enable(false);
    setStatus("Confirming your payment and starting the build…", "info");
    fetch(String(endpoint).replace(/\/$/, "") + "/setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        program_name: data.program_name,
        accent: data.accent || "",
        logo: data.logo || "",
        endpoint_ids: ids,
        deliver_to: data.deliver_to || "",
      }),
    })
      .then(function (resp) {
        return resp.json().catch(function () {
          return {};
        }).then(function (body) {
          return { resp: resp, body: body };
        });
      })
      .then(function (result) {
        var resp = result.resp;
        var body = result.body;
        if (resp.ok && body.ok) {
          var promise = typeof body.promise === "string" ? body.promise : "";
          setStatus(
            (
              "Thank you. Your reports are being generated and the download link goes to the " +
              "address you gave. " +
              promise +
              " The link stays valid for 30 days."
            )
              .replace(/\s+/g, " ")
              .trim(),
            "ok",
          );
          return;
        }
        enable(true);
        setStatus(
          String(body.error || "The service answered " + resp.status + ". Nothing was charged twice; try again."),
          "err",
        );
      })
      .catch(function () {
        enable(true);
        setStatus("Could not reach the setup service. Your payment is safe; try again in a minute.", "err");
      });
  });
})();
