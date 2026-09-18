"""Shared pieces of the compliance-bundle Lambdas (docs/compliance-bundle-plan.md).

Two handlers share this module: the post-checkout setup form (setup_handler.py) and the Stripe
webhook (webhook_handler.py). Everything here is standard library at import time; boto3 is
imported lazily inside the functions that touch AWS so the pure logic runs under pytest with no
account.

Ported from gtfs-scorecard's infra/program-bundle/common.py (same author, same operator, same
shape of product) and renamed to this product's domain. A quarterly refresh's recurring
re-dispatch (gtfs's refresh_handler.py) and the daily undelivered-order audit (gtfs's
reconcile_handler.py) are deliberately NOT ported in this first version -- see
docs/compliance-bundle-plan.md for what that leaves open, and why the subscription plans are not
sold until they are. What is ported is the part that makes a purchase safe to accept at all:
signature verification, the paid-and-for-this-product check, and the anti-arbitrage rule that a
cheap subscription must not unlock a wide bundle it never paid for.

Two things differ from the reference implementation on purpose:

**Secrets are read from SSM Parameter Store at run time, never from the environment.** The
Stripe restricted key, the webhook signing secret, and the GitHub dispatch token live as
``SecureString`` parameters under ``SSM_PREFIX`` (see :data:`SECRET_PARAMETERS`), so none of the
three is ever a Terraform variable, a Lambda environment variable, or a value in Terraform
state. A parameter that does not exist, cannot be read, or holds the wrong kind of key reads as
``""``, and every caller treats ``""`` as "closed": :func:`payments_enabled` is false, the
webhook refuses every signature, and nothing is built.

**The fulfillment workflow is never handed buyer data.** This repository is public, and GitHub
prints every step's environment, including values that came from ``workflow_dispatch`` inputs,
in a run log anyone can read. So the setup route writes the validated request to the private
artifacts bucket under a fresh random name (:func:`store_request`) and dispatches the workflow
with that name alone (:func:`dispatch_bundle_workflow`). The name is not a credential: reading
the object needs the workflow's AWS role.

Environment (set by Terraform; nothing here is secret):
  SSM_PREFIX            parameter path of the three secrets: /fhir-scorecard/compliance-bundle
  STRIPE_MODE           "test" or "live"; the restricted key must be an rk_<mode>_ key
  GITHUB_REPO           owner/name, e.g. ChelseaKR/fhir-scorecard
  WORKFLOW_FILE         compliance-bundle.yml
  WORKFLOW_REF          branch to dispatch on, default main
  STRIPE_PRICE_IDS      JSON of terraform's stripe_price_ids: plan key -> price id
  PAYMENTS_ENABLED      "1" while the purchase surface is open; anything else closes it
  SUBSCRIPTIONS_TABLE   DynamoDB table of subscriptions (hash: id)
  BUNDLES_TABLE         DynamoDB table of bundle capabilities (hash: bundle_id)
  ARTIFACTS_BUCKET      private bucket: orders in compliance-requests/, archives in
                        compliance-bundles/
  ALLOW_ORIGIN          CORS origin of the setup form (never '*')
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_ORIGIN = "https://fhir.chelseakr.com"
GITHUB_API = "https://api.github.com"
STRIPE_API = "https://api.stripe.com"
# Kept in step with fhir_scorecard.bundle.DOWNLOAD_DAYS and the S3 lifecycle rules in
# infra/compliance-bundle/main.tf.
DOWNLOAD_DAYS = 30
# Stripe's own recommended replay tolerance for the signed timestamp.
SIGNATURE_TOLERANCE_SECONDS = 300

# The three secrets, by the last segment of their SSM parameter name under SSM_PREFIX. The
# owner creates them (docs/compliance-bundle-owner-steps.md); Terraform only grants the Lambdas
# read access to the path and never sees a value.
STRIPE_KEY_PARAMETER = "stripe-restricted-key"
WEBHOOK_SECRET_PARAMETER = "stripe-webhook-secret"  # noqa: S105 - a parameter name, not a secret
GITHUB_TOKEN_PARAMETER = "github-dispatch-token"  # noqa: S105 - a parameter name, not a secret
SECRET_PARAMETERS = (STRIPE_KEY_PARAMETER, WEBHOOK_SECRET_PARAMETER, GITHUB_TOKEN_PARAMETER)
# A rotated secret reaches a warm Lambda within this long, without a redeploy.
SECRET_CACHE_SECONDS = 300
STRIPE_MODES = ("test", "live")
# Where the setup route leaves a validated order for the workflow to collect. Expires with the
# archive (main.tf's lifecycle rule), because it holds the buyer's email address.
REQUESTS_PREFIX = "compliance-requests/"
ORDER_REF_RE = re.compile(r"[a-f0-9]{32}")


# What each price buys (docs/compliance-bundle-plan.md, "Prices"). The setup route holds the
# endpoint list to the cap of the price that was actually paid for. Keys match terraform's
# stripe_price_ids and data/bundle/plan.json's products.
#
# These are ceilings, not entitlements, and the difference is the whole point of
# `_inherited_cap`. A refresh renews a bundle somebody already bought and covers the endpoints
# *that bundle* covers, so the 70 on the two refresh rows is only the widest a refresh could
# ever be, never what one buys on its own. Read as an entitlement it made the cheapest product
# dominate the most expensive one: a quarterly refresh would deliver the same 70-endpoint archive
# the widest one-time bundle sells, because the caps were equal and nothing required the bundle
# first (the same arbitrage gtfs-scorecard's own PLAN_AGENCY_CAPS comment documents catching).
#
# The two refresh plans are not sold at launch: nothing re-dispatches a subscription's second
# quarter yet (docs/compliance-bundle-plan.md). Terraform refuses a price id for either of them,
# so price_plans() below can never recognize one, and a refresh checkout is refused unread.
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


_SECRET_CACHE: dict[str, tuple[float, str]] = {}


def _read_parameter(name: str) -> str:
    """One SecureString from SSM, decrypted. Raises on anything but a readable value."""
    import boto3

    region = os.environ.get("AWS_REGION", "us-west-2")
    ssm = boto3.client("ssm", region_name=region)
    value = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    return str(value)


def secret(parameter: str) -> str:
    """The value of one of :data:`SECRET_PARAMETERS`, or ``""`` when it is not configured.

    ``""`` covers every way a secret can be absent: no ``SSM_PREFIX``, a parameter the owner has
    not created yet, one this role cannot read, or an SSM outage. Each caller treats ``""`` as
    closed, so an absent secret can only ever refuse a purchase, never half-accept one. A miss
    is logged by parameter name and error class, never by value, and is not cached, so creating
    the parameter takes effect on the next request.
    """
    if parameter not in SECRET_PARAMETERS:
        raise ValueError(f"not a compliance-bundle secret: {parameter}")
    cached = _SECRET_CACHE.get(parameter)
    if cached is not None and time.monotonic() - cached[0] < SECRET_CACHE_SECONDS:
        return cached[1]
    prefix = os.environ.get("SSM_PREFIX", "").rstrip("/")
    if not prefix.startswith("/"):
        return ""
    try:
        value = _read_parameter(f"{prefix}/{parameter}").strip()
    except Exception as err:  # ParameterNotFound, AccessDenied, throttling, no network
        print(
            json.dumps(
                {"event": "secret_unavailable", "parameter": parameter, "error": type(err).__name__}
            )
        )
        return ""
    if value:
        _SECRET_CACHE[parameter] = (time.monotonic(), value)
    return value


def stripe_mode() -> str:
    """ "test" or "live" as Terraform set it, or ``""`` for anything else (which closes)."""
    mode = os.environ.get("STRIPE_MODE", "")
    return mode if mode in STRIPE_MODES else ""


def stripe_key() -> str:
    """The restricted Stripe key, or ``""`` when there is none this Lambda may use.

    Only an ``rk_<mode>_`` key for the mode Terraform declared is ever returned. A full secret
    key (``sk_``) can refund, charge, and read every customer on the account and is never used
    by a deployed function, and a key for the other mode would read none of the checkouts the
    configured prices produce -- a live purchase would be refused after the card was charged.
    """
    mode = stripe_mode()
    key = secret(STRIPE_KEY_PARAMETER)
    if not mode or not key:
        return ""
    if not key.startswith(f"rk_{mode}_"):
        # Nothing derived from the key is logged, not even its prefix.
        print(json.dumps({"event": "stripe_key_refused", "mode": mode}))
        return ""
    return key


def payments_enabled() -> bool:
    """True only while the purchase surface is open *and* everything a purchase needs exists.

    ``PAYMENTS_ENABLED`` is the Terraform gate. On its own it is not enough: a payment accepted
    with no Stripe key cannot be confirmed, one with no dispatch token cannot be built, and one
    with no bucket has nowhere to go. Each of those is a buyer charged for nothing, so each one
    closes the route instead, before the checkout is claimed. No key means no checkout.
    """
    if os.environ.get("PAYMENTS_ENABLED", "0") != "1":
        return False
    if not os.environ.get("ARTIFACTS_BUCKET"):
        return False
    return bool(stripe_key()) and bool(secret(GITHUB_TOKEN_PARAMETER))


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
# GitHub: dispatch the fulfillment workflow
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


def new_order_ref() -> str:
    """A fresh 128-bit name for one dispatched order. Not the bundle id, and not derived from
    it: it appears in a public run log, so it must reveal nothing, and on its own it grants
    nothing (the object it names is readable only by the workflow's AWS role)."""
    return secrets.token_hex(16)


def request_key(order_ref: str) -> str:
    """Where :func:`store_request` puts an order, and where compliance-bundle.yml reads it."""
    if not ORDER_REF_RE.fullmatch(order_ref):
        raise ValueError("an order reference is 32 lowercase hex characters")
    return f"{REQUESTS_PREFIX}{order_ref}.json"


def store_request(order_ref: str, request: dict[str, Any]) -> None:
    """Write one validated order to the private bucket for the workflow to collect.

    Raises UpstreamError when the write fails, so the caller answers "could not start" and the
    buyer can resend the same checkout, exactly as for a failed dispatch.
    """
    body = json.dumps(request, sort_keys=True).encode()
    try:
        import boto3

        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-west-2"))
        s3.put_object(
            Bucket=os.environ["ARTIFACTS_BUCKET"],
            Key=request_key(order_ref),
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
    except Exception as err:  # botocore ClientError, EndpointConnectionError, a missing bucket
        raise UpstreamError(f"storing the order request failed: {type(err).__name__}") from err


def dispatch_bundle_workflow(order_ref: str) -> None:
    """POST a workflow_dispatch for compliance-bundle.yml naming one stored order.

    The one input is the order reference. Nothing a buyer typed and nothing that grants access
    (the bundle id is the download capability) travels in the dispatch, because GitHub prints a
    step's environment in the run log and this repository's run logs are public. GitHub returns
    204 with no body on success.
    """
    request_key(order_ref)  # refuses a malformed reference before anything is sent
    token = secret(GITHUB_TOKEN_PARAMETER)
    if not token:
        raise UpstreamError("the GitHub dispatch token is not configured")
    repo = os.environ["GITHUB_REPO"]
    workflow = os.environ.get("WORKFLOW_FILE", "compliance-bundle.yml")
    ref = os.environ.get("WORKFLOW_REF", "main")
    _request(
        "POST",
        f"{GITHUB_API}/repos/{repo}/actions/workflows/{workflow}/dispatches",
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        {"ref": ref, "inputs": {"order_ref": order_ref}},
    )


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------


def stripe_get(path: str) -> dict[str, Any]:
    """GET one Stripe object with the restricted key."""
    key = stripe_key()
    if not key:
        raise UpstreamError("the Stripe restricted key is not configured")
    out = _request("GET", f"{STRIPE_API}{path}", {"Authorization": f"Bearer {key}"})
    return out if isinstance(out, dict) else {}


def price_plans() -> dict[str, str]:
    """Map each configured Stripe price id to the plan it sells.

    Read from STRIPE_PRICE_IDS on every call. A blank id, an unknown plan key, or unreadable
    JSON recognizes nothing, and an id configured for two plans is dropped rather than guessed:
    a half-configured deploy refuses a purchase it cannot place, and never sells more than was
    paid for.
    """
    raw = os.environ.get("STRIPE_PRICE_IDS") or "{}"
    try:
        configured = json.loads(raw)
    except ValueError:
        print("STRIPE_PRICE_IDS is not readable JSON; no price is recognized")
        return {}
    if not isinstance(configured, dict):
        print("STRIPE_PRICE_IDS is not a JSON object; no price is recognized")
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
