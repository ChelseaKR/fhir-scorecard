"""The pieces of the compliance bundle that live in different files and must agree.

A plan the page sells, a price id Terraform accepts, a cap the Lambda enforces, and a secret the
Lambda reads are each written down in more than one place (plan.json, main.tf, common.py,
site.py, bundle.py). Nothing at run time compares them, so a drift between two of them would be
found by a buyer. These tests compare them.

The workflow's input contract is here too: this repository is public and GitHub prints a step's
environment, including dispatch inputs, in a run log anyone can read, so the fulfillment workflow
must take no input but the opaque order reference.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from fhir_scorecard import bundle, site

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "infra" / "compliance-bundle"
MAIN_TF = (MODULE / "main.tf").read_text(encoding="utf-8")
PLAN = json.loads((REPO / "data" / "bundle" / "plan.json").read_text(encoding="utf-8"))
WORKFLOW = REPO / ".github" / "workflows" / "compliance-bundle.yml"


@pytest.fixture(scope="module")
def common() -> Any:
    spec = importlib.util.spec_from_file_location("common_contract", MODULE / "common.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["common_contract"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("common_contract", None)


def _tf_block(name: str) -> str:
    match = re.search(rf'variable "{name}" \{{(.*?)\n\}}', MAIN_TF, re.DOTALL)
    assert match, f"main.tf has no variable {name}"
    return match.group(1)


def test_the_plans_sold_are_the_same_in_every_file(common: Any) -> None:
    tf_keys = re.findall(r"^\s+(\w+)\s+=\s+\"\"$", _tf_block("stripe_price_ids"), re.MULTILINE)
    assert tuple(tf_keys) == site.BUNDLE_PLANS
    assert tuple(PLAN["products"]) == site.BUNDLE_PLANS
    assert common.ONE_TIME_PLANS == site.BUNDLE_PLANS
    # Terraform's key validation names exactly the same plans, so it cannot accept a refresh.
    validation = re.search(r"toset\(\[(.*?)\]\)", _tf_block("stripe_price_ids"))
    assert validation
    assert tuple(re.findall(r'"(\w+)"', validation.group(1))) == site.BUNDLE_PLANS
    assert not set(common.SUBSCRIPTION_PLANS) & set(tf_keys)


def test_each_plan_label_states_the_cap_the_lambda_enforces(common: Any) -> None:
    for key in site.BUNDLE_PLANS:
        cap = common.PLAN_ENDPOINT_CAPS[key]
        assert f"up to {cap} endpoints" in PLAN["products"][key]["label"], key
    assert max(common.PLAN_ENDPOINT_CAPS[k] for k in site.BUNDLE_PLANS) == bundle.MAX_ENDPOINTS
    assert PLAN["max_endpoints"] == bundle.MAX_ENDPOINTS
    js = (REPO / "src" / "fhir_scorecard" / "assets" / "bundle-setup.js").read_text("utf-8")
    assert f"length > {bundle.MAX_ENDPOINTS})" in js


def test_the_secrets_terraform_checks_are_the_ones_the_lambda_reads(common: Any) -> None:
    names = re.search(r"secret_names\s+=\s+\[(.*?)\]", MAIN_TF)
    assert names
    assert tuple(re.findall(r'"([a-z-]+)"', names.group(1))) == common.SECRET_PARAMETERS


def test_no_secret_is_a_terraform_variable_or_a_lambda_environment_variable() -> None:
    variables = re.findall(r'variable "(\w+)"', MAIN_TF)
    for forbidden in ("stripe_secret_key", "stripe_webhook_secret", "github_token"):
        assert forbidden not in variables
    assert "sensitive" not in MAIN_TF
    for env in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "GITHUB_TOKEN"):
        assert env not in MAIN_TF


def test_the_committed_plan_is_well_formed_in_whatever_state_the_owner_left_it() -> None:
    """True before launch, during the test-mode walk-through, and after launch, so it never has
    to be edited to let the owner's own launch change through. A value the page would refuse to
    render is refused here first, where CI shows it, instead of silently selling nothing."""
    assert isinstance(PLAN["paymentsAvailable"], bool)
    assert PLAN["setup_api_base"] is None or site.bundle_setup_api(PLAN) is not None
    for key, product in PLAN["products"].items():
        url = product["checkout_url"]
        assert url is None or site._checkout_url(url) == url, key
        assert site._bundle_price(product["price"]) is not None, key
    if PLAN["paymentsAvailable"] is not True:
        assert site.bundle_offers(PLAN) == ()


def _launch_problems(plan: dict[str, Any]) -> list[str]:
    """What is wrong with an open tier: a plan the page would not sell, or a test-mode link."""
    problems = []
    sold = [o.key for o in site.bundle_offers(plan)]
    problems += [f"{key} is not on sale" for key in site.BUNDLE_PLANS if key not in sold]
    for key, product in plan["products"].items():
        if "/test_" in str(product.get("checkout_url") or ""):
            problems.append(f"{key} links to a test-mode Payment Link")
    return problems


def test_an_open_tier_sells_every_plan_and_never_through_a_test_mode_link() -> None:
    """Opening the tier with a test-mode Payment Link would publish a checkout no real card can
    complete, and opening it with a plan missing a link would quietly sell less than the page
    says. Both fail here, on the owner's launch change, before the page is deployed."""
    if PLAN["paymentsAvailable"] is not True:
        pytest.skip("the tier is not open; nothing to check yet")
    assert _launch_problems(PLAN) == []


def _opened() -> dict[str, Any]:
    opened: dict[str, Any] = json.loads(json.dumps(PLAN))
    opened["paymentsAvailable"] = True
    opened["setup_api_base"] = "https://abc123.execute-api.us-west-2.amazonaws.com"
    for key in site.BUNDLE_PLANS:
        opened["products"][key]["checkout_url"] = f"https://buy.stripe.com/live{key}"
    return opened


@pytest.mark.parametrize(
    ("link", "problem"),
    [
        ("https://buy.stripe.com/test_28E14m6Yz3", "test-mode Payment Link"),
        (None, "is not on sale"),
    ],
)
def test_negative_control_the_launch_check_refuses_a_test_link_or_a_missing_one(
    link: str | None, problem: str
) -> None:
    assert _launch_problems(_opened()) == []
    broken = _opened()
    broken["products"]["bundle_15"]["checkout_url"] = link
    found = _launch_problems(broken)
    assert found and all("bundle_15" in f for f in found)
    assert any(problem in f for f in found)


def test_the_workflow_takes_nothing_but_the_order_reference() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)
    assert list(triggers) == ["workflow_dispatch"]
    assert list(triggers["workflow_dispatch"]["inputs"]) == ["order_ref"]
    text = WORKFLOW.read_text(encoding="utf-8")
    assert set(re.findall(r"inputs\.(\w+)", text)) == {"order_ref"}


def test_the_workflow_never_publishes_the_archive_or_the_order() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["bundle"]["steps"]
    uses = [step.get("uses", "") for step in steps]
    assert not any("upload-artifact" in u for u in uses)
    # Every buyer value is masked before any step that could print one runs.
    names = [step.get("name", "") for step in steps]
    mask = names.index("Collect the order and mask what it holds")
    for later in (
        "Render the bundle",
        "Upload the archive behind its capability key",
        "Email the download link",
    ):
        assert names.index(later) > mask
    assert "bundle_order mask request.json" in steps[mask]["run"]


def test_the_role_the_workflow_assumes_trusts_main_only() -> None:
    assert '"repo:${var.github_repo}:ref:refs/heads/main"' in MAIN_TF
    assert 'WORKFLOW_REF  = "main"' in MAIN_TF
