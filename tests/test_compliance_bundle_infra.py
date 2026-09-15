"""Unit tests for the compliance-bundle Lambdas (infra/compliance-bundle): the Stripe signature
check, the post-checkout setup route (paid gate, what was actually bought, the plan's endpoint
cap, idempotent session claim, dispatch), and the webhook's event handling.

The modules load from their files, exactly as the Lambda runtime loads them (``common`` is
imported by bare name from ``setup_handler``/``webhook_handler``, so it has to be importable
that way here too); boto3 stays out of the import path entirely (these fakes never call it), and
the two network calls this code makes (GitHub dispatch, Stripe session/line-item reads) are
monkeypatched. fhir_scorecard itself is imported normally, from the installed package, since it
is not a mocked boundary -- this is the same package under test everywhere else in this suite.

This is a leaner fake DynamoDB than gtfs-scorecard's own test harness for the same handlers: it
interprets only the two condition-expression shapes this code actually writes
(``attribute_not_exists(x)`` and ``attribute_exists(x)``) and the one scan filter shape
(``begins_with(x, :a) OR begins_with(x, :b)``), rather than a general expression grammar. A test
that needed a third shape would need to extend it, which is the point: the fake can only pass a
test whose assertion is about a condition this code really writes.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE_DIR = REPO / "infra" / "compliance-bundle"
SIGNING_SECRET = "test-signing-secret"  # noqa: S105 - a test fixture value, not a credential

PRICE_BUNDLE_15 = "price_bundle15example"
PRICE_BUNDLE_70 = "price_bundle70example"
PRICE_REFRESH_QTR = "price_refreshqtrexample"
PRICE_REFRESH_YR = "price_refreshyrexample"
PRICE_FOREIGN = "price_somethingelseexample"
CONFIGURED_PRICES = json.dumps(
    {
        "bundle_15": PRICE_BUNDLE_15,
        "bundle_70": PRICE_BUNDLE_70,
        "refresh_qtr": PRICE_REFRESH_QTR,
        "refresh_yr": PRICE_REFRESH_YR,
    }
)


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, MODULE_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _module_path() -> Any:
    sys.path.insert(0, str(MODULE_DIR))
    yield
    sys.path.remove(str(MODULE_DIR))
    for name in ("common", "setup_handler", "webhook_handler"):
        sys.modules.pop(name, None)


@pytest.fixture()
def common(_module_path: Any) -> Any:
    return _load("common")


@pytest.fixture()
def setup_handler(common: Any) -> Any:
    return _load("setup_handler")


@pytest.fixture()
def webhook_handler(common: Any) -> Any:
    return _load("webhook_handler")


# ---------------------------------------------------------------------------
# A minimal fake DynamoDB table
# ---------------------------------------------------------------------------


class ConditionFailed(Exception):
    """Named to match what the handlers detect by class name substring."""


class FakeTable:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def _key(self, key: dict[str, Any]) -> str:
        return next(iter(key.values()))

    def get_item(self, Key: dict[str, Any]) -> dict[str, Any]:
        row = self.items.get(self._key(Key))
        return {"Item": row} if row is not None else {}

    def put_item(
        self, Item: dict[str, Any], ConditionExpression: str | None = None, **_: Any
    ) -> None:
        pk = next(iter(Item.values())) if "bundle_id" not in Item else Item["bundle_id"]
        pk = Item.get("bundle_id") or Item.get("id")
        exists = pk in self.items
        if ConditionExpression == "attribute_not_exists(bundle_id)" and exists:
            raise ConditionFailed("ConditionalCheckFailedException")
        if ConditionExpression == "attribute_not_exists(#k)" and exists:
            raise ConditionFailed("ConditionalCheckFailedException")
        self.items[pk] = dict(Item)

    def update_item(
        self,
        Key: dict[str, Any],
        UpdateExpression: str,
        ExpressionAttributeValues: dict[str, Any] | None = None,
        ExpressionAttributeNames: dict[str, str] | None = None,
        ConditionExpression: str | None = None,
    ) -> None:
        pk = self._key(Key)
        exists = pk in self.items
        if ConditionExpression == "attribute_exists(#k)" and not exists:
            raise ConditionFailed("ConditionalCheckFailedException")
        row = self.items.setdefault(pk, dict(Key))
        # UpdateExpression is always "SET #name0 = :val0, #name1 = :val1, ..." in this code, and
        # every #placeholder used as a name has a matching :placeholder used as its value (with
        # the same suffix after stripping the sigil) -- true for both the #f0/:v0-style
        # generated names (_record_subscription's update path) and the fixed #s/:s, #p/:p style
        # names (webhook_handler, setup_handler's _mark_dispatched). Applying every pair rather
        # than parsing the expression string is correct for exactly that reason, and would stop
        # being correct the day this code wrote a name whose value used a different suffix.
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        for placeholder, field in names.items():
            value_key = ":" + placeholder.lstrip("#")
            if value_key in values:
                row[field] = values[value_key]
        self.items[pk] = row

    def scan(
        self,
        FilterExpression: str | None = None,
        ExpressionAttributeValues: dict[str, Any] | None = None,
        ExclusiveStartKey: Any = None,
    ) -> dict[str, Any]:
        rows = list(self.items.values())
        if FilterExpression and "begins_with" in FilterExpression:
            prefixes = list((ExpressionAttributeValues or {}).values())
            rows = [
                r
                for r in rows
                if any(str(next(iter(r.values()), "")).startswith(p) for p in prefixes)
                or any(
                    str(r.get("bundle_id", "")).startswith(p) for p in prefixes if "bundle_id" in r
                )
            ]
        return {"Items": rows}


# ---------------------------------------------------------------------------
# Stripe signature verification
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str, *, timestamp: int | None = None) -> str:
    t = timestamp if timestamp is not None else int(time.time())
    signed = f"{t}.".encode() + body
    v1 = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


def test_verify_stripe_signature_accepts_a_correctly_signed_body(common: Any) -> None:
    body = b'{"type": "checkout.session.completed"}'
    header = _sign(body, SIGNING_SECRET)
    assert common.verify_stripe_signature(body, header, SIGNING_SECRET)


def test_verify_stripe_signature_rejects_a_tampered_body(common: Any) -> None:
    body = b'{"type": "checkout.session.completed"}'
    header = _sign(body, SIGNING_SECRET)
    assert not common.verify_stripe_signature(b'{"type": "evil"}', header, SIGNING_SECRET)


def test_verify_stripe_signature_rejects_the_wrong_secret(common: Any) -> None:
    body = b"{}"
    header = _sign(body, "a-different-secret")
    assert not common.verify_stripe_signature(body, header, SIGNING_SECRET)


def test_verify_stripe_signature_rejects_a_replayed_old_timestamp(common: Any) -> None:
    body = b"{}"
    old = int(time.time()) - common.SIGNATURE_TOLERANCE_SECONDS - 60
    header = _sign(body, SIGNING_SECRET, timestamp=old)
    assert not common.verify_stripe_signature(body, header, SIGNING_SECRET)


def test_verify_stripe_signature_rejects_a_missing_header_or_secret(common: Any) -> None:
    body = b"{}"
    assert not common.verify_stripe_signature(body, "", SIGNING_SECRET)
    assert not common.verify_stripe_signature(body, _sign(body, SIGNING_SECRET), "")


def test_verify_stripe_signature_rejects_a_header_with_no_v1(common: Any) -> None:
    body = b"{}"
    assert not common.verify_stripe_signature(body, f"t={int(time.time())}", SIGNING_SECRET)


# ---------------------------------------------------------------------------
# webhook_handler.apply_event
# ---------------------------------------------------------------------------


def test_apply_event_notes_a_checkout_for_a_configured_price(
    webhook_handler: Any, common: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STRIPE_PRICE_IDS", CONFIGURED_PRICES)
    monkeypatch.setattr(
        webhook_handler,
        "checkout_plan",
        lambda session_id: (PRICE_BUNDLE_15, "bundle_15"),
    )
    bundles = FakeTable()
    outcome = webhook_handler.apply_event(
        "checkout.session.completed",
        {
            "object": {
                "id": "cs_test_1",
                "mode": "payment",
                "customer_details": {"email": "a@b.test"},
            }
        },
        subscriptions=FakeTable(),
        bundles=bundles,
    )
    assert outcome == "noted"
    assert f"{common.CHECKOUT_PREFIX}cs_test_1" in bundles.items


def test_apply_event_ignores_a_checkout_for_a_foreign_price(
    webhook_handler: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhook_handler, "checkout_plan", lambda session_id: None)
    bundles = FakeTable()
    outcome = webhook_handler.apply_event(
        "checkout.session.completed",
        {"object": {"id": "cs_test_2"}},
        subscriptions=FakeTable(),
        bundles=bundles,
    )
    assert outcome == "ignored"
    assert bundles.items == {}


def test_apply_event_upserts_a_new_subscription(
    webhook_handler: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        webhook_handler, "subscription_plan", lambda obj: (PRICE_REFRESH_QTR, "refresh_qtr")
    )
    subscriptions = FakeTable()
    outcome = webhook_handler.apply_event(
        "customer.subscription.created",
        {"object": {"id": "sub_1", "status": "active", "customer": "cus_1"}},
        subscriptions=subscriptions,
        bundles=FakeTable(),
    )
    assert outcome == "updated"
    assert subscriptions.items["sub_1"]["status"] == "active"


def test_apply_event_never_creates_a_row_for_a_subscription_on_a_foreign_price(
    webhook_handler: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subscription event whose price is not one of the two refresh prices must only update a
    row that already exists, never create one -- the negative control on the ``ours=`` gate."""
    monkeypatch.setattr(webhook_handler, "subscription_plan", lambda obj: None)
    subscriptions = FakeTable()
    outcome = webhook_handler.apply_event(
        "customer.subscription.updated",
        {"object": {"id": "sub_unrelated", "status": "active"}},
        subscriptions=subscriptions,
        bundles=FakeTable(),
    )
    assert outcome == "ignored"
    assert subscriptions.items == {}


def test_apply_event_cancels_a_subscription(
    webhook_handler: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        webhook_handler, "subscription_plan", lambda obj: (PRICE_REFRESH_QTR, "refresh_qtr")
    )
    subscriptions = FakeTable()
    subscriptions.items["sub_1"] = {"id": "sub_1", "status": "active"}
    outcome = webhook_handler.apply_event(
        "customer.subscription.deleted",
        {"object": {"id": "sub_1"}},
        subscriptions=subscriptions,
        bundles=FakeTable(),
    )
    assert outcome == "canceled"
    assert subscriptions.items["sub_1"]["status"] == "canceled"


def test_apply_event_ignores_an_unrecognised_event_type(webhook_handler: Any) -> None:
    outcome = webhook_handler.apply_event(
        "some.other.event", {}, subscriptions=FakeTable(), bundles=FakeTable()
    )
    assert outcome == "ignored"


# ---------------------------------------------------------------------------
# setup_handler.setup
# ---------------------------------------------------------------------------


def _event(body: dict[str, Any]) -> dict[str, Any]:
    return {"body": json.dumps(body)}


def _paid_session(
    price: str, *, email: str = "buyer@example.org", subscription: str | None = None
) -> dict[str, Any]:
    session: dict[str, Any] = {
        "id": "cs_test_paid",
        "payment_status": "paid",
        "created": int(time.time()),
        "customer_details": {"email": email},
        "customer": "cus_1",
    }
    if subscription:
        session["subscription"] = subscription
    return session


@pytest.fixture()
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYMENTS_ENABLED", "1")
    monkeypatch.setenv("STRIPE_PRICE_IDS", CONFIGURED_PRICES)
    monkeypatch.setenv("GITHUB_REPO", "ChelseaKR/fhir-scorecard")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-fake-token")


def test_setup_refuses_outright_when_payments_are_disabled(
    setup_handler: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PAYMENTS_ENABLED", raising=False)
    resp = setup_handler.setup(_event({"session_id": "cs_x"}))
    assert resp["statusCode"] == 503


def test_setup_happy_path_dispatches_and_claims_the_session(
    setup_handler: Any, _env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_handler, "stripe_get", lambda path: _paid_session(PRICE_BUNDLE_15))
    monkeypatch.setattr(
        setup_handler, "checkout_plan", lambda session_id: (PRICE_BUNDLE_15, "bundle_15")
    )
    dispatched: list[dict[str, str]] = []
    monkeypatch.setattr(
        setup_handler, "dispatch_bundle_workflow", lambda inputs: dispatched.append(inputs)
    )
    bundles = FakeTable()
    monkeypatch.setattr(setup_handler, "table", lambda name: bundles)

    resp = setup_handler.setup(
        _event(
            {
                "session_id": "cs_test_paid",
                "program_name": "Acme Compliance Partners",
                "accent": "#123456",
                "endpoint_ids": "one-payer, two-payer",
            }
        )
    )
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    assert body["ok"] is True
    assert len(dispatched) == 1
    assert dispatched[0]["program_name"] == "Acme Compliance Partners"
    assert dispatched[0]["endpoint_ids"] == "one-payer,two-payer"
    # The session claim exists under the session# prefix and is marked dispatched.
    from common import SESSION_PREFIX  # type: ignore[import-not-found]

    claim = bundles.items[f"{SESSION_PREFIX}cs_test_paid"]
    assert claim["dispatched"] is True


def test_setup_refuses_an_endpoint_list_over_the_paid_plans_cap(
    setup_handler: Any, _env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_handler, "stripe_get", lambda path: _paid_session(PRICE_BUNDLE_15))
    monkeypatch.setattr(
        setup_handler, "checkout_plan", lambda session_id: (PRICE_BUNDLE_15, "bundle_15")
    )
    monkeypatch.setattr(setup_handler, "table", lambda name: FakeTable())
    ids = ",".join(f"endpoint-{i}" for i in range(20))  # bundle_15 caps at 15

    resp = setup_handler.setup(
        _event(
            {
                "session_id": "cs_test_paid",
                "program_name": "Acme",
                "endpoint_ids": ids,
            }
        )
    )
    assert resp["statusCode"] == 400
    assert "at most 15" in json.loads(resp["body"])["error"]


def test_setup_refuses_a_checkout_for_something_else_on_the_account(
    setup_handler: Any, _env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_handler, "stripe_get", lambda path: _paid_session(PRICE_FOREIGN))
    monkeypatch.setattr(setup_handler, "checkout_plan", lambda session_id: None)
    monkeypatch.setattr(setup_handler, "table", lambda name: FakeTable())

    resp = setup_handler.setup(
        _event({"session_id": "cs_test_paid", "program_name": "Acme", "endpoint_ids": "one"})
    )
    assert resp["statusCode"] == 403
    assert "not for a FHIR Scorecard" in json.loads(resp["body"])["error"]


def test_setup_refuses_an_unpaid_session(
    setup_handler: Any, _env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    unpaid = _paid_session(PRICE_BUNDLE_15)
    unpaid["payment_status"] = "unpaid"
    monkeypatch.setattr(setup_handler, "stripe_get", lambda path: unpaid)
    monkeypatch.setattr(setup_handler, "table", lambda name: FakeTable())

    resp = setup_handler.setup(
        _event({"session_id": "cs_test_paid", "program_name": "Acme", "endpoint_ids": "one"})
    )
    assert resp["statusCode"] == 402


def test_setup_double_submit_is_idempotent_and_does_not_redispatch(
    setup_handler: Any, _env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_handler, "stripe_get", lambda path: _paid_session(PRICE_BUNDLE_15))
    monkeypatch.setattr(
        setup_handler, "checkout_plan", lambda session_id: (PRICE_BUNDLE_15, "bundle_15")
    )
    dispatched: list[dict[str, str]] = []
    monkeypatch.setattr(
        setup_handler, "dispatch_bundle_workflow", lambda inputs: dispatched.append(inputs)
    )
    bundles = FakeTable()
    monkeypatch.setattr(setup_handler, "table", lambda name: bundles)

    form = _event(
        {"session_id": "cs_test_paid", "program_name": "Acme", "endpoint_ids": "one-payer"}
    )
    first = setup_handler.setup(form)
    second = setup_handler.setup(form)

    assert first["statusCode"] == 200
    assert second["statusCode"] == 409
    assert len(dispatched) == 1  # the second submission never re-dispatched


def test_setup_refuses_a_refresh_with_no_prior_bundle_before_claiming_the_session(
    setup_handler: Any, _env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control for the anti-arbitrage rule: a refresh with nothing to renew must be
    refused, AND the session must be left unclaimed -- checked directly against the fake table,
    not just via the response -- so the buyer can still cancel an unused checkout."""
    monkeypatch.setattr(
        setup_handler,
        "stripe_get",
        lambda path: _paid_session(PRICE_REFRESH_QTR, subscription="sub_new"),
    )
    monkeypatch.setattr(
        setup_handler, "checkout_plan", lambda session_id: (PRICE_REFRESH_QTR, "refresh_qtr")
    )
    bundles = FakeTable()
    monkeypatch.setattr(setup_handler, "table", lambda name: bundles)

    resp = setup_handler.setup(
        _event({"session_id": "cs_test_paid", "program_name": "Acme", "endpoint_ids": "one-payer"})
    )
    assert resp["statusCode"] == 403
    assert "renews a bundle you have already bought" in json.loads(resp["body"])["error"]
    from common import SESSION_PREFIX  # type: ignore[import-not-found]

    assert f"{SESSION_PREFIX}cs_test_paid" not in bundles.items


def test_setup_refresh_inherits_the_cap_of_the_bundle_it_renews(
    setup_handler: Any, _env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refresh must be capped at what the ONE-TIME bundle it renews actually bought, never at
    the refresh price's own ceiling -- the arbitrage setup_handler.py's own docstring names:
    without this a cheap subscription would unlock the widest archive."""
    from common import PLAN_ENDPOINT_CAPS, SESSION_PREFIX  # type: ignore[import-not-found]

    bundles = FakeTable()
    # A prior bundle_15 purchase on the same email, recorded the way the setup route itself
    # would have recorded it.
    bundles.items[f"{SESSION_PREFIX}cs_prior"] = {
        "bundle_id": f"{SESSION_PREFIX}cs_prior",
        "consumed_by": "a" * 32,
        "plan": "bundle_15",
        "email": "buyer@example.org",
        "dispatched": True,
    }
    monkeypatch.setattr(setup_handler, "table", lambda name: bundles)
    monkeypatch.setattr(
        setup_handler,
        "stripe_get",
        lambda path: _paid_session(PRICE_REFRESH_QTR, subscription="sub_new"),
    )
    monkeypatch.setattr(
        setup_handler, "checkout_plan", lambda session_id: (PRICE_REFRESH_QTR, "refresh_qtr")
    )
    dispatched: list[dict[str, str]] = []
    monkeypatch.setattr(
        setup_handler, "dispatch_bundle_workflow", lambda inputs: dispatched.append(inputs)
    )

    # 20 endpoints is over bundle_15's cap (15) but under refresh_qtr's own ceiling (70): must
    # still be refused, at 15, because that is what the renewed bundle bought.
    ids = ",".join(f"endpoint-{i}" for i in range(20))
    resp = setup_handler.setup(
        _event({"session_id": "cs_test_paid", "program_name": "Acme", "endpoint_ids": ids})
    )
    assert resp["statusCode"] == 400
    assert "at most 15" in json.loads(resp["body"])["error"]
    assert PLAN_ENDPOINT_CAPS["refresh_qtr"] == 70  # confirms the ceiling really is wider


def test_download_refuses_a_malformed_bundle_id(setup_handler: Any) -> None:
    resp = setup_handler.download("not-hex-and-wrong-length")
    assert resp["statusCode"] == 404


def test_download_reports_expired_link(setup_handler: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle_id = "c" * 32
    bundles = FakeTable()
    bundles.items[bundle_id] = {"bundle_id": bundle_id, "expires_at": 1}  # long past
    monkeypatch.setattr(setup_handler, "table", lambda name: bundles)
    resp = setup_handler.download(bundle_id)
    assert resp["statusCode"] == 404
    assert "expired" in resp["body"].lower()


def test_download_reports_unissued_link(
    setup_handler: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_handler, "table", lambda name: FakeTable())
    resp = setup_handler.download("d" * 32)
    assert resp["statusCode"] == 404
    assert "never issued" in resp["body"]
