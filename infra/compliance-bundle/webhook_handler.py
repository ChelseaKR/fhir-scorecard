"""Stripe webhook for the compliance-bundle tier (docs/compliance-bundle-plan.md).

One endpoint, ``POST /webhook``. Every request is verified against the endpoint's signing
secret before the body is parsed; an unsigned or mis-signed request is refused with 400 and
nothing is read from it.

The webhook is the record of subscription state, not the trigger for a build. A build starts
from the setup form (setup_handler.py) after the buyer has told us the organization's details.
So this handler does little:

- ``checkout.session.completed``: note the session, so a buyer who closes the tab before the
  setup form can be found from the Stripe dashboard and helped by hand. Only a checkout for one
  of this product's prices is noted (its line items are read with the restricted key); a
  checkout for anything else on the same Stripe account is ignored. If Stripe cannot be read the
  session is noted as ``unverified`` rather than failing the delivery, so an outage never gets
  this endpoint disabled.
- ``customer.subscription.created`` / ``updated``: upsert the subscription's status and price. A
  status other than ``active`` or ``trialing`` stops the refresh, and so does a price that is
  not one of the two refresh prices.
- ``customer.subscription.deleted``: mark it canceled. The row is kept, not deleted, so a
  cancellation is a fact with a date rather than an absence.

A subscription event for a price that is not one of the refresh prices never creates a row. It
only updates a row that already exists, which is a tracked subscription that moved off the
refresh prices; the refresh then stops for it.

Anything else is acknowledged with 200 and ignored; Stripe retries on non-2xx, and an unknown
event type is not a reason to make it retry.

Ported from gtfs-scorecard's infra/program-bundle/webhook_handler.py with names changed to this
product's domain; the event-handling logic is unchanged.
"""

from __future__ import annotations

import json
import os
from typing import Any

from common import (
    CHECKOUT_PREFIX,
    UpstreamError,
    checkout_plan,
    json_response,
    now_iso,
    subscription_plan,
    table,
    verify_stripe_signature,
)

ACTIVE_STATUSES = ("active", "trialing")


def _headers(event: dict[str, Any]) -> dict[str, str]:
    return {k.lower(): v for k, v in (event.get("headers") or {}).items()}


def _raw_body(event: dict[str, Any]) -> bytes:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64

        return base64.b64decode(body)
    return body.encode() if isinstance(body, str) else bytes(body)


def _checkout_note_plan(session_id: str) -> str:
    """The plan a completed checkout bought, "" when it is not this product's, or "unverified"
    when Stripe could not be read to tell."""
    if not session_id.startswith("cs_"):
        return ""
    try:
        bought = checkout_plan(session_id)
    except UpstreamError as err:
        return "" if err.status == 404 else "unverified"
    return bought[1] if bought else ""


def _update_subscription(subscriptions: Any, *, ours: bool, **kwargs: Any) -> bool:
    """Run one update on the subscriptions table. A subscription on a refresh price is
    upserted; any other is written only if its row already exists. Returns False when that
    condition left nothing to update."""
    if not ours:
        kwargs["ConditionExpression"] = "attribute_exists(#k)"
        kwargs["ExpressionAttributeNames"] = {**kwargs["ExpressionAttributeNames"], "#k": "id"}
    try:
        subscriptions.update_item(**kwargs)
    except Exception as err:  # boto3's ConditionalCheckFailedException, by name
        if not ours and (
            "ConditionalCheckFailed" in type(err).__name__ or "ConditionalCheckFailed" in str(err)
        ):
            return False
        raise
    return True


def apply_event(event_type: str, data: dict[str, Any], *, subscriptions: Any, bundles: Any) -> str:
    """Apply one verified event to the tables. Returns a one-word outcome for the response body
    and the log."""
    obj = data.get("object") or {}
    if event_type == "checkout.session.completed":
        session_id = str(obj.get("id") or "")
        plan = _checkout_note_plan(session_id)
        if not plan:
            return "ignored"
        bundles.put_item(
            Item={
                "bundle_id": f"{CHECKOUT_PREFIX}{session_id}",
                "mode": obj.get("mode", ""),
                "plan": plan,
                "email": (obj.get("customer_details") or {}).get("email", ""),
                "seen_at": now_iso(),
            }
        )
        return "noted"
    if event_type not in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        return "ignored"
    bought = subscription_plan(obj)
    key = {"id": str(obj.get("id") or "")}
    if event_type == "customer.subscription.deleted":
        written = _update_subscription(
            subscriptions,
            ours=bought is not None,
            Key=key,
            UpdateExpression="SET #s = :s, canceled_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "canceled", ":t": now_iso()},
        )
        return "canceled" if written else "ignored"
    status = str(obj.get("status") or "")
    price, plan = bought if bought else ("", "")
    written = _update_subscription(
        subscriptions,
        ours=bought is not None,
        Key=key,
        UpdateExpression="SET #s = :s, customer = :c, #p = :p, #n = :n, updated_at = :t",
        ExpressionAttributeNames={"#s": "status", "#p": "price", "#n": "plan"},
        ExpressionAttributeValues={
            ":s": "active" if status in ACTIVE_STATUSES else status or "unknown",
            ":c": str(obj.get("customer") or ""),
            ":p": price,
            ":n": plan,
            ":t": now_iso(),
        },
    )
    return "updated" if written else "ignored"


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """API Gateway (HTTP API v2 payload) entrypoint."""
    method = event.get("requestContext", {}).get("http", {}).get("method", "POST")
    if method != "POST":
        return json_response(405, {"ok": False, "error": "POST only."})
    raw = _raw_body(event)
    signature = _headers(event).get("stripe-signature", "")
    if not verify_stripe_signature(raw, signature, os.environ.get("STRIPE_WEBHOOK_SECRET", "")):
        return json_response(400, {"ok": False, "error": "Signature check failed."})
    try:
        payload = json.loads(raw.decode())
    except ValueError:
        return json_response(400, {"ok": False, "error": "Body is not JSON."})
    if not isinstance(payload, dict):
        return json_response(400, {"ok": False, "error": "Body is not an event."})
    outcome = apply_event(
        str(payload.get("type") or ""),
        payload.get("data") or {},
        subscriptions=table("SUBSCRIPTIONS_TABLE"),
        bundles=table("BUNDLES_TABLE"),
    )
    return json_response(200, {"ok": True, "outcome": outcome})
