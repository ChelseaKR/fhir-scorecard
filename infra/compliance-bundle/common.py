"""Shared pieces of the compliance-bundle Lambdas (docs/compliance-bundle-plan.md).

Two handlers share this module: the post-checkout setup form (setup_handler.py) and the Stripe
webhook (webhook_handler.py). Everything here is standard library at import time; boto3 is
imported lazily inside the functions that touch AWS so the pure logic runs under pytest with no
account.

Ported from gtfs-scorecard's infra/program-bundle/common.py (same author, same operator, same
shape of product) and renamed to this product's domain. A quarterly refresh's recurring
re-dispatch (gtfs's refresh_handler.py) and the daily undelivered-order audit (gtfs's
reconcile_handler.py) are deliberately NOT ported in this first version -- see
docs/compliance-bundle-plan.md for what that leaves open. What is ported is the part that makes
a purchase safe to accept at all: signature verification, the paid-and-for-this-product check,
and the anti-arbitrage rule that a cheap subscription must not unlock a wide bundle it never
paid for.

Environment (set by Terraform):
  GITHUB_TOKEN          fine-scoped token: actions: write to dispatch the fulfilment workflow
  GITHUB_REPO           owner/name, e.g. ChelseaKR/fhir-scorecard
  WORKFLOW_FILE         compliance-bundle.yml
  WORKFLOW_REF          branch to dispatch on, default main
  STRIPE_SECRET_KEY     restricted key: read checkout sessions only
  STRIPE_WEBHOOK_SECRET signing secret of the one webhook endpoint
  STRIPE_PRICE_IDS      JSON of terraform's stripe_price_ids: plan key -> price id
  PAYMENTS_ENABLED      "1" while the purchase surface is open; anything else closes it
  SUBSCRIPTIONS_TABLE   DynamoDB table of subscriptions (hash: id)
  BUNDLES_TABLE         DynamoDB table of bundle capabilities (hash: bundle_id)
  ARTIFACTS_BUCKET      where compliance-bundle.yml puts compliance-bundles/<id>/bundle.zip
  ALLOW_ORIGIN          CORS origin of the setup form (never '*')
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_ORIGIN = "https://fhir.chelseakr.com"
GITHUB_API = "https://api.github.com"
STRIPE_API = "https://api.stripe.com"
# Kept in step with fhir_scorecard.bundle.DOWNLOAD_DAYS and the S3 lifecycle rule for
# compliance-bundles/ in infra/artifacts/main.tf.
DOWNLOAD_DAYS = 30
# Stripe's own recommended replay tolerance for the signed timestamp.
SIGNATURE_TOLERANCE_SECONDS = 300


# What each price buys (docs/compliance-bundle-plan.md, "Prices"). The setup route holds the
# endpoint list to the cap of the price that was actually paid for. Keys match terraform's
# stripe_price_ids and data/bundle/plan.json's products.
#
# These are ceilings, not entitlements, and the difference is the whole point of
# `_inherited_cap`. A refresh renews a bundle somebody already bought and covers the endpoints
# *that bundle* covers, so the 70 on the two refresh rows is only the widest a refresh could
# ever be, never what one buys on its own. Read as an entitlement it made the cheapest product
# dominate the most expensive one: $99 a quarter would deliver the same 70-endpoint archive the
# $699 bundle sells, because the caps were equal and nothing required the bundle first (the same
# arbitrage gtfs-scorecard's own PLAN_AGENCY_CAPS comment documents catching).
PLAN_ENDPOINT_CAPS: dict[str, int] = {
    "bundle_15": 15,
    "bundle_70": 70,
    "refresh_qtr": 70,
    "refresh_yr": 70,
}
# The one-time archives, and the subscriptions that renew one. Every key of PLAN_ENDPOINT_CAPS
# belongs to exactly one of these two.
ONE_TIME_PLANS = ("bundle_15", "bundle_70")
SUBSCRIPTION_PLANS = ("refresh_qtr", "refresh_yr")
# Key prefixes in the bundles table. A bare bundle id is a capability row; a `session#` row is
# the setup route's claim on a Checkout Session, and a `checkout#` row is the webhook's note of
# a completed checkout. Neither prefixed row carries `expires_at`, so both outlive the 30-day
# capability TTL -- which is what lets a refresh bought in one quarter find the bundle bought in
# an earlier one (setup_handler._inherited_cap).
SESSION_PREFIX = "session#"
CHECKOUT_PREFIX = "checkout#"
# One page of line items is plenty: every Payment Link this product creates has exactly one, and
# a longer list is refused unread.
_LINE_ITEMS_LIMIT = 10


class UpstreamError(RuntimeError):
    """A GitHub or Stripe call failed; the message is safe to log, not to show.

    ``status`` is the HTTP status when the service answered, else None.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def payments_enabled() -> bool:
    """True only while Terraform has opened the purchase surface. The API route for the setup
    form is removed when the gate is closed; this is the same gate read again inside the
    Lambda, so a stale route or a direct invoke cannot build anything either."""
    return os.environ.get("PAYMENTS_ENABLED", "0") == "1"


# ---------------------------------------------------------------------------
# HTTP responses
# ---------------------------------------------------------------------------


def cors_headers(content_type: str = "application/json") -> dict[str, str]:
    return {
        "Content-Type": content_type,
        "Access-Control-Allow-Origin": os.environ.get("ALLOW_ORIGIN", DEFAULT_ORIGIN),
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def json_response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": cors_headers(), "body": json.dumps(body)}


def html_response(status: int, title: str, message: str) -> dict[str, Any]:
    page = (
        f"<!doctype html><meta charset=utf-8><title>{title}</title>"
        "<body style='font-family:system-ui;max-width:34rem;margin:4rem auto;padding:0 1rem'>"
        f"<h1>{title}</h1><p>{message}</p>"
        "<p><a href='https://fhir.chelseakr.com/'>Back to FHIR Scorecard</a></p>"
    )
    return {"statusCode": status, "headers": cors_headers("text/html"), "body": page}


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def epoch_in(days: int) -> int:
    return int(time.time()) + days * 86400


# ---------------------------------------------------------------------------
# GitHub: dispatch the fulfilment workflow
# ---------------------------------------------------------------------------


def _request(
    method: str, url: str, headers: dict[str, str], payload: dict[str, Any] | None = None
) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - api.github.com / api.stripe.com only
    for key, value in headers.items():
        req.add_header(key, value)
    req.add_header("User-Agent", "fhir-scorecard-compliance-bundle")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed hosts only
            raw = resp.read().decode()
    except urllib.error.HTTPError as err:
        raise UpstreamError(f"{method} {url} -> HTTP {err.code}", status=err.code) from err
    except (urllib.error.URLError, OSError) as err:
        raise UpstreamError(f"{method} {url} failed: {err}") from err
    return json.loads(raw) if raw.strip() else {}


def dispatch_bundle_workflow(inputs: dict[str, str]) -> None:
    """POST a workflow_dispatch for compliance-bundle.yml with the given inputs.

    GitHub returns 204 with no body on success. Inputs are the workflow's declared inputs and
    nothing else; the workflow re-validates every one.
    """
    repo = os.environ["GITHUB_REPO"]
    workflow = os.environ.get("WORKFLOW_FILE", "compliance-bundle.yml")
    ref = os.environ.get("WORKFLOW_REF", "main")
    _request(
        "POST",
        f"{GITHUB_API}/repos/{repo}/actions/workflows/{workflow}/dispatches",
        {
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
        },
        {"ref": ref, "inputs": inputs},
    )


def dispatch_key(bundle_id: str) -> str:
    """A public, one-way name for one bundle's workflow runs.

    The bundle id is the download capability, and this repository is public, so it cannot be
    the artifact name or the concurrency group in compliance-bundle.yml. sha256 over a 128-bit
    token, truncated to 64 bits: enough to separate every bundle this product will ever sell,
    and no help at all to somebody trying to recover the id it came from.
    """
    return hashlib.sha256(bundle_id.encode()).hexdigest()[:16]


def workflow_inputs(request: dict[str, Any]) -> dict[str, str]:
    """The workflow_dispatch inputs for a stored or validated request dict."""
    endpoint_ids = request.get("endpoint_ids") or []
    if isinstance(endpoint_ids, list | tuple):
        endpoint_ids = ",".join(str(a) for a in endpoint_ids)
    bundle_id = str(request["bundle_id"])
    return {
        "bundle_id": bundle_id,
        "program_name": str(request["program_name"]),
        "accent": str(request.get("accent") or ""),
        "logo": str(request.get("logo") or ""),
        "endpoint_ids": str(endpoint_ids),
        "deliver_to": str(request["deliver_to"]),
        "cadence": str(request.get("cadence") or "one_time"),
        "dispatch_key": dispatch_key(bundle_id),
        "promised_by": str(request.get("promised_by") or ""),
    }


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------


def stripe_get(path: str) -> dict[str, Any]:
    """GET one Stripe object with the restricted secret key."""
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise UpstreamError("STRIPE_SECRET_KEY is not configured")
    out = _request("GET", f"{STRIPE_API}{path}", {"Authorization": f"Bearer {key}"})
    return out if isinstance(out, dict) else {}


def price_plans() -> dict[str, str]:
    """Map each configured Stripe price id to the plan it sells.

    Read from STRIPE_PRICE_IDS on every call. A blank id, an unknown plan key, or unreadable
    JSON recognises nothing, and an id configured for two plans is dropped rather than guessed:
    a half-configured deploy refuses a purchase it cannot place, and never sells more than was
    paid for.
    """
    raw = os.environ.get("STRIPE_PRICE_IDS") or "{}"
    try:
        configured = json.loads(raw)
    except ValueError:
        print("STRIPE_PRICE_IDS is not readable JSON; no price is recognised")
        return {}
    if not isinstance(configured, dict):
        print("STRIPE_PRICE_IDS is not a JSON object; no price is recognised")
        return {}
    plans: dict[str, str] = {}
    ambiguous: set[str] = set()
    for plan, price in configured.items():
        if plan not in PLAN_ENDPOINT_CAPS or not isinstance(price, str) or not price.strip():
            continue
        price = price.strip()
        if price in plans:
            ambiguous.add(price)
        plans[price] = plan
    if ambiguous:
        print(f"STRIPE_PRICE_IDS maps {len(ambiguous)} price id(s) to two plans; dropping them")
    return {price: plan for price, plan in plans.items() if price not in ambiguous}


def _price_id(item: object) -> str:
    """The price id of one line item or subscription item."""
    if not isinstance(item, dict):
        return ""
    price = item.get("price")
    if isinstance(price, dict):
        return str(price.get("id") or "")
    return str(price or "")


def plan_for_items(items: object) -> tuple[str, str] | None:
    """(price id, plan) when ``items`` is exactly one item on a configured price, else None.
    Every Payment Link this product creates has one line item, so anything else was not bought
    through one of them."""
    if not isinstance(items, list) or len(items) != 1:
        return None
    price = _price_id(items[0])
    plan = price_plans().get(price)
    return (price, plan) if plan else None


def checkout_plan(session_id: str) -> tuple[str, str] | None:
    """What a Checkout Session bought: (price id, plan), or None when it was not one of this
    product's prices.

    Reads the session's line items with the same restricted key ("Checkout Sessions: Read"
    covers them). Raises UpstreamError when Stripe cannot be read, so a caller never mistakes an
    outage for a foreign purchase.
    """
    quoted = urllib.parse.quote(session_id, safe="")
    listing = stripe_get(f"/v1/checkout/sessions/{quoted}/line_items?limit={_LINE_ITEMS_LIMIT}")
    if listing.get("has_more"):
        return None
    return plan_for_items(listing.get("data"))


def subscription_plan(subscription: dict[str, Any]) -> tuple[str, str] | None:
    """(price id, plan) for a Stripe subscription object on one of the two refresh prices, read
    from the object itself; None for anything else."""
    items = subscription.get("items")
    found = plan_for_items(items.get("data") if isinstance(items, dict) else None)
    if found is None or found[1] not in SUBSCRIPTION_PLANS:
        return None
    return found


def verify_stripe_signature(
    payload: bytes, header: str, secret: str, *, now: int | None = None
) -> bool:
    """Check a Stripe-Signature header against the raw body.

    Stripe signs ``"{t}.{payload}"`` with HMAC-SHA256 and sends ``t=<unix>,v1=<hex>[,v1=<hex>...]``.
    Any v1 that matches within the replay tolerance is accepted; anything else is refused,
    including a header with no timestamp, an unparseable timestamp, or an empty secret.
    """
    if not secret or not header:
        return False
    timestamp = ""
    candidates: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            candidates.append(value)
    if not timestamp.isdigit() or not candidates:
        return False
    current = int(time.time()) if now is None else now
    if abs(current - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in candidates)


# ---------------------------------------------------------------------------
# DynamoDB
# ---------------------------------------------------------------------------


def table(env_name: str) -> Any:
    import boto3

    region = os.environ.get("AWS_REGION", "us-west-2")
    return boto3.resource("dynamodb", region_name=region).Table(os.environ[env_name])


def scan_all(source: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Every item of a table, following LastEvaluatedKey to the end.

    A single ``scan`` returns one page, and a caller that reads only the first one sees a prefix
    of the table and cannot tell that it did. Any ``FilterExpression`` is passed through, but a
    filter is a bandwidth saving, never the correctness argument: DynamoDB applies it after the
    read, and a caller must still check what it got.
    """
    rows: list[dict[str, Any]] = []
    while True:
        page = source.scan(**kwargs)
        rows.extend(page.get("Items") or [])
        start = page.get("LastEvaluatedKey")
        if not start:
            return rows
        kwargs = {**kwargs, "ExclusiveStartKey": start}


def bundle_row(
    request: dict[str, Any],
    *,
    source: str,
    session_id: str = "",
    deliver_by_epoch: int | None = None,
) -> dict[str, Any]:
    """The capability row for one bundle: who it is for, when it expires, and when it was
    promised by.

    ``deliver_by_epoch`` is written once, at checkout, and never recomputed. A promise
    recalculated later is a promise that moves, and this one carries a refund. It is absent on a
    refresh row on purpose: the two-business-day commitment is made at a purchase, and a
    subscription's quarterly archive is a different promise. A row without it can still be
    reported undelivered; it simply cannot breach a deadline nobody made.
    """
    row = {
        "bundle_id": request["bundle_id"],
        "deliver_to": request["deliver_to"],
        "program_name": request["program_name"],
        "source": source,
        "session_id": session_id,
        "created_at": now_iso(),
        "expires_at": epoch_in(DOWNLOAD_DAYS),
    }
    if deliver_by_epoch is not None:
        row["deliver_by_epoch"] = int(deliver_by_epoch)
    return row
