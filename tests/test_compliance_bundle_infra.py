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
import re
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


def test_apply_event_ignores_an_unrecognized_event_type(webhook_handler: Any) -> None:
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


SSM_PREFIX = "/fhir-scorecard/compliance-bundle"
STRIPE_TEST_KEY = "rk_test_fake-key-for-tests"  # not key-shaped, so secret scanners pass it
GITHUB_FAKE_TOKEN = "github_pat_fakeDispatchTokenForTests"  # noqa: S105 - a fixture, not a credential
#: The parameter store the Lambdas would read, keyed by full parameter name. A test removes or
#: replaces an entry to take a secret away; `_read_parameter` raises for an absent one, exactly
#: as SSM's ParameterNotFound does.
PARAMETERS = {
    f"{SSM_PREFIX}/stripe-restricted-key": STRIPE_TEST_KEY,
    f"{SSM_PREFIX}/stripe-webhook-secret": SIGNING_SECRET,
    f"{SSM_PREFIX}/github-dispatch-token": GITHUB_FAKE_TOKEN,
}


class ParameterNotFound(Exception):
    """Named like SSM's own, which common.secret treats like any other failure."""


@pytest.fixture()
def ssm(common: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store = dict(PARAMETERS)
    reads: list[str] = []

    def _read(name: str) -> str:
        reads.append(name)
        if name not in store:
            raise ParameterNotFound(name)
        return store[name]

    monkeypatch.setattr(common, "_read_parameter", _read)
    monkeypatch.setenv("SSM_PREFIX", SSM_PREFIX)
    monkeypatch.setenv("STRIPE_MODE", "test")
    store["__reads__"] = reads  # type: ignore[assignment]
    return store


@pytest.fixture()
def _env(ssm: dict[str, Any], setup_handler: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Everything a purchase needs, present. Records every stored order and every dispatch."""
    monkeypatch.setenv("PAYMENTS_ENABLED", "1")
    monkeypatch.setenv("STRIPE_PRICE_IDS", CONFIGURED_PRICES)
    monkeypatch.setenv("GITHUB_REPO", "ChelseaKR/fhir-scorecard")
    monkeypatch.setenv("ARTIFACTS_BUCKET", "fhir-scorecard-compliance-bundles-test")
    record: dict[str, list[Any]] = {"stored": [], "dispatched": []}
    monkeypatch.setattr(
        setup_handler, "store_request", lambda ref, order: record["stored"].append((ref, order))
    )
    monkeypatch.setattr(
        setup_handler, "dispatch_bundle_workflow", lambda ref: record["dispatched"].append(ref)
    )
    return record


def test_setup_refuses_outright_when_payments_are_disabled(
    setup_handler: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PAYMENTS_ENABLED", raising=False)
    resp = setup_handler.setup(_event({"session_id": "cs_x"}))
    assert resp["statusCode"] == 503


def test_setup_happy_path_dispatches_and_claims_the_session(
    setup_handler: Any, _env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_handler, "stripe_get", lambda path: _paid_session(PRICE_BUNDLE_15))
    monkeypatch.setattr(
        setup_handler, "checkout_plan", lambda session_id: (PRICE_BUNDLE_15, "bundle_15")
    )
    dispatched = _env["dispatched"]
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
    order_ref = dispatched[0]
    assert re.fullmatch(r"[0-9a-f]{32}", order_ref)
    # The order itself went to the private bucket, under the reference the dispatch names.
    [(stored_ref, order)] = _env["stored"]
    assert stored_ref == order_ref
    assert order["program_name"] == "Acme Compliance Partners"
    assert order["endpoint_ids"] == ["one-payer", "two-payer"]
    assert order["deliver_to"] == "buyer@example.org"
    assert order["max_endpoints"] == 15
    assert order["bundle_id"] == body["bundle_id"] != order_ref
    assert order["promised_by"]
    # The session claim exists under the session# prefix and is marked dispatched.
    from common import SESSION_PREFIX  # type: ignore[import-not-found]

    claim = bundles.items[f"{SESSION_PREFIX}cs_test_paid"]
    assert claim["dispatched"] is True


def test_setup_refuses_an_endpoint_list_over_the_paid_plans_cap(
    setup_handler: Any, _env: Any, monkeypatch: pytest.MonkeyPatch
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
    setup_handler: Any, _env: Any, monkeypatch: pytest.MonkeyPatch
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
    setup_handler: Any, _env: Any, monkeypatch: pytest.MonkeyPatch
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
    setup_handler: Any, _env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_handler, "stripe_get", lambda path: _paid_session(PRICE_BUNDLE_15))
    monkeypatch.setattr(
        setup_handler, "checkout_plan", lambda session_id: (PRICE_BUNDLE_15, "bundle_15")
    )
    dispatched = _env["dispatched"]
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
    setup_handler: Any, _env: Any, monkeypatch: pytest.MonkeyPatch
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
    setup_handler: Any, _env: Any, monkeypatch: pytest.MonkeyPatch
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


# ---------------------------------------------------------------------------
# Fails closed: no key means no checkout is accepted
# ---------------------------------------------------------------------------


def _paid_setup(setup_handler: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Wire a paid bundle_15 checkout and record whether Stripe was even asked."""
    calls: dict[str, Any] = {"stripe": 0, "bundles": FakeTable()}

    def _stripe(path: str) -> dict[str, Any]:
        calls["stripe"] += 1
        return _paid_session(PRICE_BUNDLE_15)

    monkeypatch.setattr(setup_handler, "stripe_get", _stripe)
    monkeypatch.setattr(
        setup_handler, "checkout_plan", lambda session_id: (PRICE_BUNDLE_15, "bundle_15")
    )
    monkeypatch.setattr(setup_handler, "table", lambda name: calls["bundles"])
    return calls


_FORM = {"session_id": "cs_test_paid", "program_name": "Acme", "endpoint_ids": "one-payer"}


def test_positive_control_a_fully_configured_setup_accepts_the_checkout(
    setup_handler: Any, _env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _paid_setup(setup_handler, monkeypatch)
    assert setup_handler.setup(_event(_FORM))["statusCode"] == 200
    assert calls["stripe"] == 1
    assert len(_env["dispatched"]) == 1


def _drop(ssm: dict[str, Any], name: str) -> None:
    key = f"{SSM_PREFIX}/{name}"
    assert key in ssm, f"{key} was never configured, so removing it proves nothing"
    del ssm[key]


@pytest.mark.parametrize(
    "take_away",
    [
        pytest.param(lambda ssm, mp: _drop(ssm, "stripe-restricted-key"), id="no-stripe-key"),
        pytest.param(lambda ssm, mp: _drop(ssm, "github-dispatch-token"), id="no-dispatch-token"),
        pytest.param(lambda ssm, mp: mp.delenv("SSM_PREFIX"), id="no-ssm-prefix"),
        pytest.param(lambda ssm, mp: mp.delenv("ARTIFACTS_BUCKET"), id="no-bucket"),
        pytest.param(lambda ssm, mp: mp.setenv("PAYMENTS_ENABLED", "0"), id="gate-closed"),
        pytest.param(lambda ssm, mp: mp.setenv("STRIPE_MODE", "staging"), id="unknown-mode"),
        pytest.param(
            lambda ssm, mp: ssm.update({f"{SSM_PREFIX}/stripe-restricted-key": "sk_test_full"}),
            id="a-full-secret-key",
        ),
        pytest.param(
            lambda ssm, mp: ssm.update({f"{SSM_PREFIX}/stripe-restricted-key": "rk_live_x"}),
            id="a-live-key-in-test-mode",
        ),
        pytest.param(
            lambda ssm, mp: ssm.update({f"{SSM_PREFIX}/stripe-restricted-key": "  "}),
            id="a-blank-key",
        ),
    ],
)
def test_setup_refuses_before_touching_stripe_without_everything_a_purchase_needs(
    setup_handler: Any,
    _env: Any,
    ssm: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    take_away: Any,
) -> None:
    """Negative controls for the fail-closed gate: take one thing away and the route answers
    503 before Stripe is read, before the checkout is claimed, and before anything is stored or
    dispatched -- the buyer keeps an unused checkout, not a consumed one and no archive."""
    calls = _paid_setup(setup_handler, monkeypatch)
    take_away(ssm, monkeypatch)
    resp = setup_handler.setup(_event(_FORM))
    assert resp["statusCode"] == 503
    assert "Setup is closed" in json.loads(resp["body"])["error"]
    assert calls["stripe"] == 0
    assert calls["bundles"].items == {}
    assert _env["stored"] == [] and _env["dispatched"] == []


def test_stripe_get_refuses_to_call_stripe_without_a_usable_key(
    common: Any, ssm: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    requested: list[str] = []
    monkeypatch.setattr(common, "_request", lambda *a, **k: requested.append(a[1]) or {})
    _drop(ssm, "stripe-restricted-key")
    with pytest.raises(common.UpstreamError, match="not configured"):
        common.stripe_get("/v1/checkout/sessions/cs_x")
    assert requested == []


def test_a_secret_is_read_once_and_a_missing_one_is_not_remembered(
    common: Any, ssm: dict[str, Any]
) -> None:
    reads = ssm["__reads__"]
    assert common.secret("stripe-restricted-key") == STRIPE_TEST_KEY
    assert common.secret("stripe-restricted-key") == STRIPE_TEST_KEY
    assert reads.count(f"{SSM_PREFIX}/stripe-restricted-key") == 1
    _drop(ssm, "github-dispatch-token")
    assert common.secret("github-dispatch-token") == ""
    # Creating the parameter takes effect on the next request, not after the cache expires.
    ssm[f"{SSM_PREFIX}/github-dispatch-token"] = GITHUB_FAKE_TOKEN
    assert common.secret("github-dispatch-token") == GITHUB_FAKE_TOKEN


def test_a_secret_name_outside_the_three_is_a_programming_error(common: Any) -> None:
    with pytest.raises(ValueError, match="not a compliance-bundle secret"):
        common.secret("aws-root-password")


def test_an_unreadable_secret_is_logged_by_name_never_by_value(
    common: Any, ssm: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    _drop(ssm, "stripe-webhook-secret")
    assert common.secret("stripe-webhook-secret") == ""
    logged = capsys.readouterr().out
    assert "stripe-webhook-secret" in logged and "ParameterNotFound" in logged
    assert SIGNING_SECRET not in logged


# ---------------------------------------------------------------------------
# The dispatch carries no buyer data (this repository's run logs are public)
# ---------------------------------------------------------------------------


def test_the_dispatch_names_the_stored_order_and_carries_nothing_the_buyer_typed(
    setup_handler: Any, _env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _paid_setup(setup_handler, monkeypatch)
    form = {
        "session_id": "cs_test_paid",
        "program_name": "Example Compliance Partners",
        "endpoint_ids": "one-payer, two-payer",
        "deliver_to": "reports@example-compliance.test",
        "logo": "",
    }
    assert setup_handler.setup(_event(form))["statusCode"] == 200
    [order_ref] = _env["dispatched"]
    [(_, order)] = _env["stored"]
    for value in (
        order["bundle_id"],
        order["deliver_to"],
        order["program_name"],
        "one-payer",
        "buyer@example.org",
        "cs_test_paid",
    ):
        assert value not in json.dumps(order_ref)


def test_dispatch_bundle_workflow_posts_only_the_order_reference(
    common: Any, ssm: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, dict[str, str], Any]] = []
    monkeypatch.setattr(
        common,
        "_request",
        lambda method, url, headers, payload=None: sent.append((method, url, headers, payload)),
    )
    monkeypatch.setenv("GITHUB_REPO", "ChelseaKR/fhir-scorecard")
    common.dispatch_bundle_workflow("a" * 32)
    [(method, url, headers, payload)] = sent
    assert method == "POST"
    assert url.endswith(
        "/repos/ChelseaKR/fhir-scorecard/actions/workflows/compliance-bundle.yml/dispatches"
    )
    assert payload == {"ref": "main", "inputs": {"order_ref": "a" * 32}}
    assert headers["Authorization"] == f"Bearer {GITHUB_FAKE_TOKEN}"


@pytest.mark.parametrize("ref", ["", "A" * 32, "a" * 31, "../../etc/passwd", "a" * 32 + "\n"])
def test_dispatch_refuses_a_malformed_order_reference(
    common: Any, ssm: dict[str, Any], ref: str
) -> None:
    with pytest.raises(ValueError, match="32 lowercase hex"):
        common.dispatch_bundle_workflow(ref)


def test_dispatch_refuses_without_the_token(
    common: Any, ssm: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common, "_request", lambda *a, **k: pytest.fail("GitHub was called"))
    _drop(ssm, "github-dispatch-token")
    with pytest.raises(common.UpstreamError, match="not configured"):
        common.dispatch_bundle_workflow("b" * 32)


class _FakeS3:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("AccessDenied")
        self.puts.append(kwargs)


def _fake_boto3(monkeypatch: pytest.MonkeyPatch, s3: _FakeS3) -> None:
    fake = type(sys)("boto3")
    fake.client = lambda service, region_name=None: s3  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "boto3", fake)


def test_store_request_writes_one_encrypted_object_under_the_reference(
    common: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    s3 = _FakeS3()
    _fake_boto3(monkeypatch, s3)
    monkeypatch.setenv("ARTIFACTS_BUCKET", "bucket-x")
    common.store_request("c" * 32, {"bundle_id": "d" * 32, "deliver_to": "a@b.test"})
    [put] = s3.puts
    assert put["Bucket"] == "bucket-x"
    assert put["Key"] == f"compliance-requests/{'c' * 32}.json"
    assert put["ServerSideEncryption"] == "AES256"
    assert json.loads(put["Body"]) == {"bundle_id": "d" * 32, "deliver_to": "a@b.test"}


def test_a_failed_store_is_an_upstream_error_and_nothing_is_dispatched(
    setup_handler: Any, common: Any, _env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setup route answers "could not start" for a store failure exactly as for a failed
    dispatch, and the claim is left unfinished so the same checkout can be sent again."""
    calls = _paid_setup(setup_handler, monkeypatch)
    _fake_boto3(monkeypatch, _FakeS3(fail=True))
    monkeypatch.setattr(setup_handler, "store_request", common.store_request)
    resp = setup_handler.setup(_event(_FORM))
    assert resp["statusCode"] == 502
    assert _env["dispatched"] == []
    from common import SESSION_PREFIX  # type: ignore[import-not-found]

    assert calls["bundles"].items[f"{SESSION_PREFIX}cs_test_paid"]["dispatched"] is False


# ---------------------------------------------------------------------------
# The webhook entrypoint: signature first, secret from SSM, mode, idempotence
# ---------------------------------------------------------------------------


def _webhook_event(payload: dict[str, Any], secret: str | None = SIGNING_SECRET) -> dict[str, Any]:
    body = json.dumps(payload)
    headers = {"Stripe-Signature": _sign(body.encode(), secret)} if secret else {}
    return {"requestContext": {"http": {"method": "POST"}}, "headers": headers, "body": body}


_COMPLETED = {
    "type": "checkout.session.completed",
    "livemode": False,
    "data": {
        "object": {"id": "cs_test_w1", "mode": "payment", "customer_details": {"email": "a@b.test"}}
    },
}


@pytest.fixture()
def webhook_tables(
    webhook_handler: Any, ssm: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> Any:
    bundles, subscriptions = FakeTable(), FakeTable()
    monkeypatch.setattr(
        webhook_handler, "table", lambda name: bundles if name == "BUNDLES_TABLE" else subscriptions
    )
    monkeypatch.setattr(
        webhook_handler, "checkout_plan", lambda session_id: (PRICE_BUNDLE_15, "bundle_15")
    )
    return bundles


def test_webhook_notes_a_signed_checkout(webhook_handler: Any, webhook_tables: Any) -> None:
    resp = webhook_handler.handler(_webhook_event(_COMPLETED))
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["outcome"] == "noted"
    assert "checkout#cs_test_w1" in webhook_tables.items


def test_webhook_redelivery_is_idempotent(webhook_handler: Any, webhook_tables: Any) -> None:
    """Stripe redelivers until it sees a 2xx; the second delivery rewrites the same row."""
    for _ in range(3):
        assert webhook_handler.handler(_webhook_event(_COMPLETED))["statusCode"] == 200
    assert list(webhook_tables.items) == ["checkout#cs_test_w1"]
    assert webhook_tables.items["checkout#cs_test_w1"]["plan"] == "bundle_15"


def test_webhook_notes_an_async_payment_that_succeeded(
    webhook_handler: Any, webhook_tables: Any
) -> None:
    event = {**_COMPLETED, "type": "checkout.session.async_payment_succeeded"}
    assert json.loads(webhook_handler.handler(_webhook_event(event))["body"])["outcome"] == "noted"


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(_webhook_event(_COMPLETED, secret=None), id="unsigned"),
        pytest.param(
            _webhook_event(_COMPLETED, secret="whsec_somebody_else"),  # noqa: S106 - a fixture
            id="wrong-secret",
        ),
        pytest.param(
            {**_webhook_event(_COMPLETED), "body": json.dumps({**_COMPLETED, "type": "x"})},
            id="tampered-body",
        ),
    ],
)
def test_webhook_refuses_an_unverified_event_before_reading_it(
    webhook_handler: Any, webhook_tables: Any, monkeypatch: pytest.MonkeyPatch, event: Any
) -> None:
    monkeypatch.setattr(webhook_handler, "apply_event", lambda *a, **k: pytest.fail("read it"))
    resp = webhook_handler.handler(event)
    assert resp["statusCode"] == 400
    assert webhook_tables.items == {}


def test_webhook_refuses_everything_until_its_secret_exists(
    webhook_handler: Any, common: Any, webhook_tables: Any, ssm: dict[str, Any]
) -> None:
    """Negative control on the SSM read: the same correctly signed event is accepted with the
    secret present and refused with it absent."""
    assert webhook_handler.handler(_webhook_event(_COMPLETED))["statusCode"] == 200
    webhook_tables.items.clear()
    _drop(ssm, "stripe-webhook-secret")
    common._SECRET_CACHE.clear()  # a cold start, which is when a removed secret is noticed
    assert webhook_handler.handler(_webhook_event(_COMPLETED))["statusCode"] == 400
    assert webhook_tables.items == {}


def test_webhook_ignores_a_verified_event_from_the_other_mode(
    webhook_handler: Any, webhook_tables: Any
) -> None:
    live = {**_COMPLETED, "livemode": True}
    resp = webhook_handler.handler(_webhook_event(live))
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["outcome"] == "ignored"
    assert webhook_tables.items == {}
