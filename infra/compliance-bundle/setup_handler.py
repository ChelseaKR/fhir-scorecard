"""Post-checkout setup form and the download route (docs/compliance-bundle-plan.md).

Two routes on the compliance-bundle API, both stateless per request:

``POST /setup``
    The page a buyer lands on after Stripe Checkout posts here with the Checkout Session id and
    the organization details (name, accent, logo, endpoint ids). The handler confirms with
    Stripe that the session is *paid* and that its one line item is one of this product's four
    prices (a paid checkout for anything else on the same Stripe account builds nothing), settles
    how many endpoints this checkout covers, mints a bundle id, validates the request with the
    pipeline's own parse_request held to that number, records the session so a replayed form
    cannot dispatch twice, stores the capability row, dispatches compliance-bundle.yml, and for a
    subscription stores the request so a future quarterly refresh could re-dispatch it (the
    re-dispatch cron itself is not part of this first version -- see
    docs/compliance-bundle-plan.md).

    **A refresh renews a bundle.** A one-time bundle is its own entitlement, but ``refresh_qtr``
    and ``refresh_yr`` are not: each requires an earlier bundle purchase on the same address and
    covers the endpoints that bundle covered (``_inherited_cap``). Without that rule the two
    refresh prices carried the 70-endpoint cap in their own right, so $99 a quarter bought the
    archive the $699 bundle sells and then cancelled -- the cheapest product on the page strictly
    dominating the most expensive one. A refresh with no bundle to renew is refused **before the
    checkout is claimed**, so the buyer is left holding an unused checkout they can cancel, not a
    consumed one and no archive.

``GET /download/{bundle_id}``
    The link in the delivery email. Looks up the capability row, and if the archive exists,
    answers with a 302 to a presigned S3 URL that lives fifteen minutes. The emailed link is
    stable for thirty days; each click mints a fresh short-lived URL, so nothing long-lived is
    ever written into an email. A bundle still being rendered answers 202 with a plain page. A
    row past its own ``expires_at`` answers "expired" on that fact, not on whether the object is
    there.

Payment is the only gate. There is no account and no password; the capability in the email is
the credential, the same posture as the free coverage-alert confirm link this project's sibling
projects use. The setup route also refuses outright unless PAYMENTS_ENABLED is "1", the same
Terraform gate that decides whether the route exists.

Ported from gtfs-scorecard's infra/program-bundle/setup_handler.py with names changed to this
product's domain (agency -> endpoint, program report -> compliance report); the entitlement and
idempotency logic is unchanged, because it is what makes a purchase safe to accept, not anything
about transit data.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.parse
from dataclasses import replace
from typing import Any, NamedTuple

from common import (
    CHECKOUT_PREFIX,
    ONE_TIME_PLANS,
    PLAN_ENDPOINT_CAPS,
    SESSION_PREFIX,
    SUBSCRIPTION_PLANS,
    UpstreamError,
    bundle_row,
    checkout_plan,
    dispatch_bundle_workflow,
    html_response,
    json_response,
    now_iso,
    payments_enabled,
    scan_all,
    stripe_get,
    table,
    workflow_inputs,
)

from fhir_scorecard import deadline
from fhir_scorecard.bundle import BundleError, archive_key, new_bundle_id, parse_request

PRESIGN_SECONDS = 15 * 60
_SESSION_ID_MAX = 200


def _session_key(session_id: str) -> str:
    return f"{SESSION_PREFIX}{session_id}"


def _session_row(session_id: str, bundle_id: str, plan: str, email: str) -> dict[str, Any]:
    """Marks a Checkout Session as consumed. Shares the bundles table under a ``session#`` key
    so one conditional put is the whole idempotency check. No ``expires_at``: the claim must
    outlive the capability row, or a replay after 30 days would build a second bundle from one
    payment.

    ``dispatched`` starts False and is set True only once GitHub has accepted the
    workflow_dispatch. The claim therefore records two different facts -- "this payment is
    spoken for" and "a build was actually started" -- so a second submission after a failed
    dispatch can finish the order instead of being told a bundle exists that does not.

    ``email`` is the address Stripe collected at that checkout: *who* this plan was sold to. A
    refresh subscription renews a bundle, so it has to be able to find the bundle it renews, and
    this row is the record written by the route that granted one.
    """
    return {
        "bundle_id": _session_key(session_id),
        "consumed_by": bundle_id,
        "plan": plan,
        "email": email,
        "dispatched": False,
    }


def _condition_failed(err: Exception) -> bool:
    """Whether a boto3 exception is the conditional check failing.

    Read by name rather than by type: the resource layer builds these classes dynamically, so
    importing the exception would tie this module to a client that only exists at runtime.
    """
    return "ConditionalCheckFailed" in type(err).__name__ or "ConditionalCheckFailed" in str(err)


def _claim_session(
    bundles: Any, session_id: str, bundle_id: str, plan: str, email: str = ""
) -> bool:
    """Atomically claim the session; False if it was already used."""
    try:
        bundles.put_item(
            Item=_session_row(session_id, bundle_id, plan, email),
            ConditionExpression="attribute_not_exists(bundle_id)",
        )
    except Exception as err:  # boto3's ConditionalCheckFailedException, by name
        if _condition_failed(err):
            return False
        raise
    return True


def _mark_dispatched(bundles: Any, session_id: str) -> None:
    """Record that the build for this session really started."""
    bundles.update_item(
        Key={"bundle_id": _session_key(session_id)},
        UpdateExpression="SET #d = :d",
        ExpressionAttributeNames={"#d": "dispatched"},
        ExpressionAttributeValues={":d": True},
    )


def _has_expired(expires_at: Any) -> bool:
    """True only when the row carries a readable epoch that has passed.

    An unreadable or absent ``expires_at`` is not evidence of expiry, so it reads as "not
    expired" and the object decides.
    """
    if isinstance(expires_at, bool):
        return False
    if isinstance(expires_at, int | float):
        return int(expires_at) <= int(time.time())
    text = str(expires_at or "").strip()
    return text.isdigit() and int(text) <= int(time.time())


def _unfinished_bundle_id(bundles: Any, session_id: str) -> str:
    """The bundle id of a claim whose build never started, else "".

    Only an explicit ``dispatched: False`` counts. A claim written before this field existed, or
    one whose Lambda died after calling GitHub, has no such record, and re-dispatching on a
    guess would build a second time from one payment. Absence of the flag is not evidence of a
    failed dispatch.
    """
    row = bundles.get_item(Key={"bundle_id": _session_key(session_id)}).get("Item") or {}
    if row.get("dispatched") is not False:
        return ""
    return str(row.get("consumed_by") or "")


class _Refused(Exception):
    """Carries the response to send instead of building anything.

    The checks in ``_paid_purchase`` each have their own status and their own sentence for the
    buyer, and every one of them means "nothing was built". Raising them keeps ``setup`` a list
    of steps rather than a ladder.
    """

    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__(str(response.get("statusCode")))
        self.response = response


def _paid_purchase(session_id: str) -> tuple[dict[str, Any], str, str]:
    """The Checkout Session, the price it was for, and the plan that price sells. Raises
    ``_Refused`` when the session was not a paid purchase of one of this product's four prices.

    Two reads, not one. That the session is *paid* says only that money moved on this Stripe
    account; what was *bought* is in the line items, and it is what decides both whether to
    build at all and how many endpoints the buyer is entitled to.
    """
    try:
        session = stripe_get(f"/v1/checkout/sessions/{urllib.parse.quote(session_id, safe='')}")
    except UpstreamError as err:
        if err.status == 404:
            raise _Refused(
                json_response(
                    404,
                    {
                        "ok": False,
                        "error": "That checkout reference is not one Stripe recognises. Open the "
                        "page Stripe sent you to after paying, address and all. If you have lost "
                        "it, reply to the receipt Stripe emailed you rather than paying again.",
                    },
                )
            ) from err
        raise _Refused(
            json_response(502, {"ok": False, "error": "Could not confirm the payment yet."})
        ) from err
    if session.get("payment_status") != "paid":
        raise _Refused(
            json_response(
                402,
                {
                    "ok": False,
                    "error": "Stripe has not settled this checkout yet, so nothing was built. "
                    "If you paid by a method that clears over a day or two, keep this page's "
                    "web address and open it again once the receipt arrives. Do not pay again.",
                },
            )
        )

    try:
        bought = checkout_plan(session_id)
    except UpstreamError as err:
        raise _Refused(
            json_response(502, {"ok": False, "error": "Could not confirm the payment yet."})
        ) from err
    if bought is None:
        raise _Refused(
            json_response(
                403,
                {
                    "ok": False,
                    "error": "This checkout was not for a FHIR Scorecard compliance report "
                    "bundle, so nothing was built.",
                },
            )
        )
    price, plan = bought
    return session, price, plan


class _Inherited(NamedTuple):
    """The cap a refresh subscription inherits, and the purchase it came from."""

    cap: int
    plan: str
    session_id: str


def _checkout_email(session: dict[str, Any]) -> str:
    """The address Stripe itself collected at this checkout, case-folded.

    Never the form's ``deliver_to``. That field is text the buyer typed, and this address is
    what decides entitlement below: if the form supplied it, anyone who guessed an
    organization's email could inherit that organization's cap for the price of the cheapest
    subscription.
    """
    details = session.get("customer_details") or {}
    return str(details.get("email") or "").strip().casefold()


def _purchase_rows(bundles: Any) -> list[dict[str, Any]]:
    """Every ``session#`` and ``checkout#`` row in the bundles table."""
    return scan_all(
        bundles,
        FilterExpression="begins_with(bundle_id, :session) OR begins_with(bundle_id, :checkout)",
        ExpressionAttributeValues={":session": SESSION_PREFIX, ":checkout": CHECKOUT_PREFIX},
    )


_NO_PRIOR_BUNDLE = (
    "A refresh renews a bundle you have already bought: it re-sends that same archive, "
    "refreshed, and it does not include a bundle of its own. Nothing was found on this "
    "email address to renew, so nothing was built and your endpoint list was not used. "
    "Buy a bundle at fhir.chelseakr.com/bundle/ with this same address and then start the "
    "refresh, or, if the bundle was bought under a different address, open an issue at "
    "github.com/ChelseaKR/fhir-scorecard/issues and the two can be linked by hand. You can "
    "cancel this subscription from the receipt Stripe emailed you."
)
_NO_CHECKOUT_EMAIL = (
    "A refresh renews a bundle you have already bought, and this checkout carries no email "
    "address, so there is no way to tell which bundle it renews. Nothing was built and your "
    "checkout has not been used. Open an issue at github.com/ChelseaKR/fhir-scorecard/issues "
    "and it can be set up by hand."
)
_CANNOT_SETTLE_PRIOR = (
    "Could not check which bundle this refresh renews just now, so nothing was built and "
    "your checkout has not been used. Open this page again in a few minutes. Do not pay again."
)
_UNSETTLED_CHECKOUT_READS = 10
_WIDEST_ONE_TIME_CAP = max(PLAN_ENDPOINT_CAPS[plan] for plan in ONE_TIME_PLANS)


def _inherited_cap(bundles: Any, session: dict[str, Any], session_id: str) -> _Inherited:
    """How many endpoints a refresh subscription covers: the cap of the bundle it renews.
    Raises ``_Refused`` when there is no such bundle.

    Two durable records answer this, and both outlive the capability row's 30-day TTL:

    ``session#`` rows
        Written here, by the route that grants a bundle, and permanent because the claim has to
        outlive the capability.

    ``checkout#`` rows
        Written by the webhook when a checkout completes. They cover a bundle bought before this
        check existed, and a buyer who paid for a bundle and never came back to the setup form
        -- they still bought it. A row whose ``plan`` the webhook could not read is settled
        against Stripe rather than counted as nothing.

    Nothing here trims or upgrades: a buyer over the inherited cap is told the number, before
    the checkout is consumed, and can send the same checkout again with a shorter list.
    """
    email = _checkout_email(session)
    if not email:
        raise _Refused(json_response(403, {"ok": False, "error": _NO_CHECKOUT_EMAIL}))
    bought: list[tuple[str, str]] = []  # (plan, the checkout that bought it)
    unsettled: list[str] = []  # checkouts whose plan nothing has read yet
    for row in _purchase_rows(bundles):
        key = str(row.get("bundle_id") or "")
        prefix = next((p for p in (SESSION_PREFIX, CHECKOUT_PREFIX) if key.startswith(p)), "")
        if not prefix:
            continue
        prior = key[len(prefix) :]
        if not prior or prior == session_id:
            continue
        if str(row.get("email") or "").strip().casefold() != email:
            continue
        plan = str(row.get("plan") or "")
        if plan in ONE_TIME_PLANS:
            bought.append((plan, prior))
        elif plan not in SUBSCRIPTION_PLANS:
            unsettled.append(prior)
    widest = max((PLAN_ENDPOINT_CAPS[plan] for plan, _ in bought), default=0)
    if unsettled and widest < _WIDEST_ONE_TIME_CAP:
        if len(unsettled) > _UNSETTLED_CHECKOUT_READS:
            print(
                json.dumps(
                    {
                        "event": "entitlement_unsettled",
                        "session_id": session_id,
                        "unsettled_checkouts": len(unsettled),
                    }
                )
            )
            raise _Refused(json_response(502, {"ok": False, "error": _CANNOT_SETTLE_PRIOR}))
        for prior in unsettled:
            try:
                settled = checkout_plan(prior)
            except UpstreamError as err:
                if err.status == 404:
                    continue
                raise _Refused(
                    json_response(502, {"ok": False, "error": _CANNOT_SETTLE_PRIOR})
                ) from err
            if settled and settled[1] in ONE_TIME_PLANS:
                bought.append((settled[1], prior))
    if not bought:
        raise _Refused(json_response(403, {"ok": False, "error": _NO_PRIOR_BUNDLE}))
    plan, prior = max(bought, key=lambda found: PLAN_ENDPOINT_CAPS[found[0]])
    return _Inherited(PLAN_ENDPOINT_CAPS[plan], plan, prior)


def _endpoint_cap(bundles: Any, session: dict[str, Any], session_id: str, plan: str) -> _Inherited:
    """The cap this checkout is held to, and where it came from.

    A one-time bundle is its own entitlement. A refresh is not: it renews one, and covers what
    that one covered. The plan's own number in PLAN_ENDPOINT_CAPS stays a ceiling in both cases,
    so an inherited cap can narrow a refresh and can never widen it.
    """
    ceiling = PLAN_ENDPOINT_CAPS[plan]
    if plan not in SUBSCRIPTION_PLANS:
        return _Inherited(ceiling, plan, session_id)
    inherited = _inherited_cap(bundles, session, session_id)
    return inherited._replace(cap=min(ceiling, inherited.cap))


def _record_subscription(
    session: dict[str, Any], request: Any, *, price: str, plan: str, entitlement: _Inherited
) -> None:
    """Store what a future quarterly refresh would need, for a subscription purchase.

    **This route does not decide whether a subscription is active.** Stripe does, and the
    webhook is where Stripe's answer arrives. Writing the whole row with ``put_item`` would
    overwrite that answer with ``status: "active"``, and the form is submitted *after* checkout,
    so the events that can lose the race are the ones that matter: a subscription cancelled from
    the Stripe receipt in the minutes before the buyer fills in this form must not come back as
    active.

    So the status is written once, in a conditional put that creates the row, and never touched
    again by this route. Every later write is an update of the fields this route owns.
    """
    if request.cadence != "quarterly" or not session.get("subscription"):
        return
    subscriptions = table("SUBSCRIPTIONS_TABLE")
    subscription_id = str(session["subscription"])
    stamp = now_iso()
    owned: dict[str, Any] = {
        "customer": str(session.get("customer") or ""),
        "price": price,
        "plan": plan,
        "endpoint_cap": entitlement.cap,
        "renews_plan": entitlement.plan,
        "renews_session": entitlement.session_id,
        "deliver_to": request.deliver_to,
        "request": json.dumps(request.as_dict()),
        "last_refresh": stamp,
    }
    try:
        subscriptions.put_item(
            Item={"id": subscription_id, "status": "active", "created_at": stamp, **owned},
            ConditionExpression="attribute_not_exists(#k)",
            ExpressionAttributeNames={"#k": "id"},
        )
        return
    except Exception as err:  # boto3's ConditionalCheckFailedException, by name
        if not _condition_failed(err):
            raise
    names = {f"#f{index}": field for index, field in enumerate(owned)}
    values = {f":v{index}": value for index, value in enumerate(owned.values())}
    assignments = ", ".join(f"{name} = :v{index}" for index, name in enumerate(names))
    subscriptions.update_item(
        Key={"id": subscription_id},
        UpdateExpression=f"SET {assignments}",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def setup(event: dict[str, Any]) -> dict[str, Any]:
    if not payments_enabled():
        return json_response(
            503,
            {
                "ok": False,
                "error": "Setup is closed right now, so nothing was built. "
                "Your checkout has not been used; try again later.",
            },
        )
    try:
        form = json.loads(event.get("body") or "{}")
    except ValueError:
        return json_response(400, {"ok": False, "error": "Could not read the form."})
    if not isinstance(form, dict):
        return json_response(400, {"ok": False, "error": "Could not read the form."})

    session_id = str(form.get("session_id") or "").strip()
    if not session_id or len(session_id) > _SESSION_ID_MAX or not session_id.startswith("cs_"):
        return json_response(400, {"ok": False, "error": "The checkout reference is missing."})

    bundles = table("BUNDLES_TABLE")
    try:
        session, price, plan = _paid_purchase(session_id)
        entitlement = _endpoint_cap(bundles, session, session_id, plan)
    except _Refused as refused:
        return refused.response

    details = session.get("customer_details") or {}
    raw = {
        "bundle_id": new_bundle_id(),
        "program_name": form.get("program_name", ""),
        "accent": form.get("accent", ""),
        "logo": form.get("logo", ""),
        "endpoint_ids": form.get("endpoint_ids", ""),
        "deliver_to": form.get("deliver_to") or details.get("email") or "",
        "cadence": "quarterly" if plan in SUBSCRIPTION_PLANS else "one_time",
    }
    try:
        request = parse_request(raw, max_endpoints=entitlement.cap)
    except BundleError as err:
        return json_response(400, {"ok": False, "error": str(err)})

    if not _claim_session(bundles, session_id, request.bundle_id, plan, _checkout_email(session)):
        unfinished = _unfinished_bundle_id(bundles, session_id)
        if not unfinished:
            return json_response(
                409, {"ok": False, "error": "This checkout already produced a bundle."}
            )
        request = replace(request, bundle_id=unfinished)
    checkout_epoch = session.get("created")
    anchored = "checkout" if isinstance(checkout_epoch, int | float) else "received"
    checkout_at = (
        deadline.from_epoch(int(checkout_epoch))
        if anchored == "checkout"
        else dt.datetime.now(dt.UTC)
    )
    row = bundle_row(
        request.as_dict(),
        source="checkout",
        session_id=session_id,
        deliver_by_epoch=deadline.deadline_epoch(checkout_at),
    )
    row["deliver_by_anchor"] = anchored
    bundles.put_item(Item=row)

    _record_subscription(session, request, price=price, plan=plan, entitlement=entitlement)

    try:
        dispatch = request.as_dict()
        dispatch["promised_by"] = deadline.spoken_date(deadline.deadline_date(checkout_at))
        dispatch_bundle_workflow(workflow_inputs(dispatch))
    except UpstreamError as err:
        print(
            json.dumps(
                {
                    "event": "dispatch_failed",
                    "session_id": session_id,
                    "bundle_id": request.bundle_id,
                    "plan": plan,
                    "deliver_to": request.deliver_to,
                    "error": str(err),
                }
            )
        )
        return json_response(
            502,
            {
                "ok": False,
                "error": "Your order is recorded but the build could not start. "
                "Nothing was charged twice. Send this form again in a few minutes and it "
                "will pick up the same order; if it keeps failing, open an issue at "
                "github.com/ChelseaKR/fhir-scorecard/issues.",
                "bundle_id": request.bundle_id,
            },
        )
    _mark_dispatched(bundles, session_id)
    return json_response(
        200,
        {
            "ok": True,
            "bundle_id": request.bundle_id,
            "deliver_by": deadline.deadline_date(checkout_at).isoformat(),
            "promise": deadline.promise_sentence(checkout_at),
        },
    )


def download(bundle_id: str) -> dict[str, Any]:
    if not bundle_id or len(bundle_id) != 32 or not all(c in "0123456789abcdef" for c in bundle_id):
        return html_response(404, "Not found", "That download link is not valid.")
    row = table("BUNDLES_TABLE").get_item(Key={"bundle_id": bundle_id}).get("Item")
    if not row:
        return html_response(
            404, "Link expired", "That download link has expired or was never issued."
        )
    if _has_expired(row.get("expires_at")):
        return html_response(
            404,
            "Link expired",
            "That download link has expired. Bundles are kept for 30 days; "
            "open an issue at github.com/ChelseaKR/fhir-scorecard/issues and it can be rebuilt.",
        )
    import boto3

    bucket = os.environ["ARTIFACTS_BUCKET"]
    key = archive_key(bundle_id)
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-west-2"))
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except Exception:  # NoSuchKey / 404 from head_object
        return html_response(
            202,
            "Still being prepared",
            "Your reports are still being generated. Try this link again in a few minutes. "
            "If this page still says the same thing an hour from now, the build did not "
            "finish: open an issue at github.com/ChelseaKR/fhir-scorecard/issues and quote "
            f"{bundle_id[:8]}, and it will be rebuilt or refunded.",
        )
    url = s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ResponseContentDisposition": (
                f'attachment; filename="compliance-reports-{bundle_id[:8]}.zip"'
            ),
        },
        ExpiresIn=PRESIGN_SECONDS,
    )
    return {
        "statusCode": 302,
        "headers": {"Location": url, "Cache-Control": "no-store"},
        "body": "",
    }


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """API Gateway (HTTP API v2 payload) entrypoint."""
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = str(event.get("rawPath") or http.get("path") or "/")
    if method == "OPTIONS":
        return {"statusCode": 204, "headers": json_response(204, {})["headers"], "body": ""}
    if method == "POST" and path.rstrip("/").endswith("/setup"):
        return setup(event)
    if method == "GET" and "/download/" in path:
        return download(path.rsplit("/download/", 1)[1].strip("/").lower())
    return json_response(404, {"ok": False, "error": "No such route."})
