#!/usr/bin/env bash
# Create the compliance report bundle's Stripe product, prices, and Payment Links, then write the
# ids where the rest of the system reads them (ported from gtfs-scorecard's scripts/stripe-setup.sh).
#
#   - infra/compliance-bundle/terraform.tfvars: stripe_mode and stripe_price_ids, with
#     payments_enabled left at "0" (opening payments is a separate, deliberate edit)
#   - data/bundle/plan.json: each plan's checkout_url. paymentsAvailable is never touched here.
#
# Prices and labels are READ from data/bundle/plan.json, so the amount Stripe charges and the
# amount the page shows come from one file and cannot disagree. Edit the prices there first.
#
# Runs against whichever mode the key in STRIPE_API_KEY belongs to. Use a TEST key first. The key
# needs write access to Products, Prices, and Payment Links and nothing else: create a restricted
# key for this one run and delete it afterward (docs/compliance-bundle-owner-steps.md). The key is
# read from the environment, never written anywhere, and at most its prefix is printed.
#
# Needs: the Stripe CLI (https://docs.stripe.com/stripe-cli), jq, python3.
#
# Usage, from the repository root:
#   STRIPE_API_KEY=rk_test_... scripts/stripe-setup.sh
#   STRIPE_API_KEY=rk_test_... scripts/stripe-setup.sh --dry-run    # print what would be created
#
# Optional environment:
#   STRIPE_TAX_CODE=txcd_...          product tax code, from Stripe's tax code list (none by default)
#   STRIPE_AUTOMATIC_TAX=1            only with an active Stripe Tax registration; also collects a
#                                     billing address, which Stripe Tax needs on a Payment Link
#   STATEMENT_DESCRIPTOR_SUFFIX=FHIR  appended to the card statement descriptor
#
# Idempotence: Stripe products and prices have no natural key, so re-running creates duplicates.
# Run it once per mode and archive anything created by mistake in the Dashboard.

set -euo pipefail

SITE="https://fhir.chelseakr.com"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLAN="$REPO_ROOT/data/bundle/plan.json"
TFVARS="$REPO_ROOT/infra/compliance-bundle/terraform.tfvars"
PLANS=(bundle_15 bundle_70)
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

if [ -z "${STRIPE_API_KEY:-}" ]; then
  echo "STRIPE_API_KEY is not set. Export a test-mode key (rk_test_...) first." >&2
  exit 2
fi
command -v jq >/dev/null || { echo "jq not found" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 2; }
if [ "$DRY_RUN" -eq 0 ]; then
  command -v stripe >/dev/null || { echo "stripe CLI not found" >&2; exit 2; }
fi

case "$STRIPE_API_KEY" in
  rk_test_*|sk_test_*) MODE="test" ;;
  rk_live_*|sk_live_*) MODE="live" ;;
  *) echo "Unrecognized key prefix; refusing to guess the mode." >&2; exit 2 ;;
esac
case "$STRIPE_API_KEY" in
  sk_*) echo "Warning: a full secret key. A restricted key with Products, Prices and Payment Links write is enough." >&2 ;;
esac
echo "Mode: $MODE  Site: $SITE  Key: ${STRIPE_API_KEY:0:8}..." >&2

# Every amount and label comes from plan.json. A plan without a whole-dollar price is refused
# rather than guessed at.
cents() {
  jq -er --arg k "$1" '.products[$k].price | select(type == "number" and . > 0 and . == floor) * 100' "$PLAN"
}
label() {
  jq -er --arg k "$1" '.products[$k].label | select(type == "string" and length > 0)' "$PLAN"
}
for key in "${PLANS[@]}"; do
  cents "$key" >/dev/null || { echo "plan.json has no whole-dollar price for $key" >&2; exit 2; }
  label "$key" >/dev/null || { echo "plan.json has no label for $key" >&2; exit 2; }
  echo "  $key: $(label "$key"), \$$(( $(cents "$key") / 100 ))" >&2
done

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run: nothing was created." >&2
  exit 0
fi

api() { stripe "$@" --api-key "$STRIPE_API_KEY"; }

product_args=(--name "FHIR Scorecard compliance report bundle"
  --description "Branded, self-contained FHIR endpoint compliance evidence reports for the endpoints you name, delivered as one archive. Each endpoint's own evidence page stays free.")
[ -n "${STRIPE_TAX_CODE:-}" ] && product_args+=(-d "tax_code=$STRIPE_TAX_CODE")
product=$(api products create "${product_args[@]}" | jq -er .id)

# Payment Links send the buyer to the setup form with the session reference. The consent box
# links to the account-level Terms of Service URL (Dashboard > Settings > Public details), which
# must be set to ${SITE}/bundle/trust/ before this runs.
success="${SITE}/bundle/setup/?session_id={CHECKOUT_SESSION_ID}"
# Plain variables, not an associative array: macOS still ships bash 3.2.
price_bundle_15="" price_bundle_70="" link_bundle_15="" link_bundle_70=""
for key in "${PLANS[@]}"; do
  price=$(api prices create --product "$product" --currency usd \
    --unit-amount "$(cents "$key")" --nickname "$key" | jq -er .id)
  link_args=(-d "line_items[0][price]=$price" -d "line_items[0][quantity]=1"
    -d "after_completion[type]=redirect"
    -d "after_completion[redirect][url]=$success"
    -d "consent_collection[terms_of_service]=required"
    -d "custom_text[submit][message]=Delivery, refund, and data terms: ${SITE}/bundle/trust/"
    -d "metadata[plan]=$key")
  if [ "${STRIPE_AUTOMATIC_TAX:-0}" = "1" ]; then
    link_args+=(-d "automatic_tax[enabled]=true" -d "billing_address_collection=required")
  fi
  if [ -n "${STATEMENT_DESCRIPTOR_SUFFIX:-}" ]; then
    link_args+=(-d "payment_intent_data[statement_descriptor_suffix]=$STATEMENT_DESCRIPTOR_SUFFIX")
  fi
  link=$(api payment_links create "${link_args[@]}" | jq -er .url)
  echo "  $key: price $price  link $link" >&2
  printf -v "price_$key" '%s' "$price"
  printf -v "link_$key" '%s' "$link"
done

cat > "$TFVARS" <<EOF
# Written by scripts/stripe-setup.sh ($MODE mode). No secret belongs in this file: the Stripe key,
# the webhook signing secret and the GitHub token live in SSM (docs/compliance-bundle-owner-steps.md).
stripe_mode = "$MODE"
stripe_price_ids = {
  bundle_15 = "${price_bundle_15}"
  bundle_70 = "${price_bundle_70}"
}
payments_enabled = "0"
EOF
echo "Wrote $TFVARS" >&2

python3 - "$PLAN" "${link_bundle_15}" "${link_bundle_70}" <<'PY'
import json, sys
path, link_15, link_70 = sys.argv[1:4]
with open(path, encoding="utf-8") as fh:
    plan = json.load(fh)
plan["products"]["bundle_15"]["checkout_url"] = link_15
plan["products"]["bundle_70"]["checkout_url"] = link_70
with open(path, "w", encoding="utf-8") as fh:
    json.dump(plan, fh, indent=2)
    fh.write("\n")
PY
echo "Wrote the checkout_url of both plans into $PLAN (paymentsAvailable unchanged)." >&2
