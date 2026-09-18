# Compliance bundle: the owner's launch checklist

Everything here is an account, credential, or apply step that only the owner takes. Nothing in
the repository can charge anyone until the last section. Work top to bottom; each step says what
it needs from the one before. Commands run from the repository root unless a step says otherwise.

What already exists: the code, the Terraform module (`infra/compliance-bundle/`, planned, never
applied), `scripts/stripe-setup.sh`, `scripts/build-lambda-package.sh`, and `data/bundle/plan.json`
with `paymentsAvailable: false`. While any one of `paymentsAvailable`, `setup_api_base`, or a
plan's `checkout_url` is missing, `/bundle/` shows no price and no Buy link, and while any secret
is missing the setup Lambda refuses every checkout before it reads Stripe.

## 0. Two decisions

**Prices.** `data/bundle/plan.json` proposes **$249** for one archive of up to 15 endpoints and
**$499** for up to 70 (the whole payer-side registry today). They are a starting guess, not
research; see "Prices" in `docs/compliance-bundle-plan.md` for the reasoning. Edit the two
`price` values now if you want different ones: `scripts/stripe-setup.sh` reads them from that
file, so Stripe and the page cannot disagree. The page publishes no price until section 11.

**Stripe account. Recommended: a new account under your existing Stripe login, not new products
on gtfs-scorecard's account (`acct_1UEJ7fAJdYOJsO05`).**

- The public business name, statement descriptor, support email, and Terms of Service URL are
  set per account. On the gtfs account, a FHIR Scorecard buyer would see "GTFS Scorecard" on the
  Checkout page, the receipt, and their card statement, and the Payment Link's terms box would
  link to gtfs-scorecard's terms. A compliance buyer who does not recognize a charge disputes it.
- A Payment Link can only append a statement-descriptor suffix. It cannot change the name, the
  support address, or the terms link.
- A second account under the same login is quick. It reuses your identity, keeps separate books
  (balance, payouts, reports, and 1099-K per product), and gives each product its own API keys,
  so a leaked key reaches one product.
- The cost is a second activation form, a second payout bank setup, and tax settings entered
  twice. That is the price of "separate books"; sharing the gtfs account would be simpler only
  until the first confused buyer.

## 1. Stripe account and settings (do once; applies to test and live)

1. Dashboard, account menu (top left), **New account**, name it "FHIR Scorecard". Activation
   (business details, bank account) can wait until section 10.
2. **Settings > Business > Public details**:
   - Public business name: `FHIR Scorecard`
   - Statement descriptor: `FHIR SCORECARD`
   - Support email: the address you want buyers' replies to reach (every buyer-facing message
     says "reply to the receipt Stripe emailed you")
   - Website: `https://fhir.chelseakr.com`
   - Terms of service: `https://fhir.chelseakr.com/bundle/trust/` (required before section 2:
     the Payment Links' consent box links to this account-level setting)
   - Privacy policy: `https://fhir.chelseakr.com/privacy/`
3. **Settings > Business > Customer emails**: turn on **Successful payments** and **Refunds**.
   Stripe's receipt is the buyer's record of the order; there is no account on the site.
   Under your own **Profile > Communication preferences**, turn on the email for **Successful
   payments**, so every order reaches you. At this volume that email is the order monitor: the
   daily reconciler gtfs-scorecard has is not ported here.
4. **Settings > Payments > Payment methods**: cards (with Apple Pay, Google Pay and Link) are
   enough. Leave delayed methods such as ACH Direct Debit off for now: a delayed payment reaches
   the setup form unsettled, and the form tells the buyer to come back later.
5. **Tax.** Decide with your tax advisor before live mode. Stripe Tax's threshold monitoring is
   free and only watches; `automatic_tax` collects nothing without an active registration, so do
   not turn it on until you have one. If you choose a product tax code, pick it from Stripe's
   list (<https://docs.stripe.com/tax/tax-codes>) and pass it as `STRIPE_TAX_CODE` in section 2;
   with a registration, also pass `STRIPE_AUTOMATIC_TAX=1`.

## 2. Test mode: product, prices, and Payment Links

1. Toggle **Test mode** (or open a sandbox of the new account).
2. **Developers > API keys > Create restricted key**, name `fhir-setup-temp`: **Products: Write**,
   **Prices: Write**, **Payment Links: Write**, everything else **None**. Copy the `rk_test_...`
   value.
3. Install the Stripe CLI once (`brew install stripe/stripe-cli/stripe`), then:

   ```sh
   read -rs STRIPE_API_KEY && export STRIPE_API_KEY   # paste rk_test_..., Enter
   scripts/stripe-setup.sh --dry-run                    # shows the two prices it will create
   scripts/stripe-setup.sh
   unset STRIPE_API_KEY
   ```

   It writes `infra/compliance-bundle/terraform.tfvars` (`stripe_mode = "test"`, both price
   ids, `payments_enabled = "0"`) and both test Payment Links into `data/bundle/plan.json`. It
   is not idempotent; run it once per mode.
4. Delete `fhir-setup-temp` (Developers > API keys > the key > Delete).
5. Create the Lambda's key: restricted key `fhir-bundle-lambda` with **Checkout Sessions: Read**
   and nothing else. (Line items are part of Checkout Sessions, so that one permission covers
   both reads the setup route makes.) Keep the `rk_test_...` value for section 4.

## 3. GitHub: the dispatch token

Settings > Developer settings > Fine-grained tokens > Generate new token:

- Resource owner `ChelseaKR`, **Only select repositories**: `fhir-scorecard`
- Repository permissions: **Actions: Read and write** (Metadata: Read is added automatically)
- Expiration: one year, and put the expiry date on your calendar. An expired token makes every
  setup form answer "the build could not start", and nothing else would tell you.

## 4. AWS: state bucket and the three secrets

The secrets live in SSM Parameter Store as `SecureString` values. Terraform never sees them; the
Lambdas read them at run time. `read -rs` keeps each value out of your shell history.

```sh
aws s3api create-bucket --bucket fhir-scorecard-tfstate-ckr --region us-west-2 \
  --create-bucket-configuration LocationConstraint=us-west-2
aws s3api put-bucket-versioning --bucket fhir-scorecard-tfstate-ckr \
  --versioning-configuration Status=Enabled

read -rs V && aws ssm put-parameter --region us-west-2 --type SecureString \
  --name /fhir-scorecard/compliance-bundle/stripe-restricted-key --value "$V"; unset V   # rk_test_... from 2.5
read -rs V && aws ssm put-parameter --region us-west-2 --type SecureString \
  --name /fhir-scorecard/compliance-bundle/github-dispatch-token --value "$V"; unset V   # the token from 3
```

The third, `stripe-webhook-secret`, comes in section 6 because the webhook URL does not exist yet.

## 5. Apply the infrastructure, payments still off

```sh
scripts/build-lambda-package.sh infra/compliance-bundle
cd infra/compliance-bundle
terraform init
terraform plan -out=tfplan      # expect 28 to add, 0 to change, 0 to destroy
terraform apply tfplan
terraform output                # api_base, webhook_url, artifacts_bucket, fulfillment_role_arn
cd ../..
```

`tfplan` and `terraform.tfvars` stay local; nothing in them is secret, but neither belongs in
git.

## 6. Stripe webhook (test mode)

Developers > Webhooks > **Add endpoint**:

- URL: the `webhook_url` output
- Events: `checkout.session.completed`, `checkout.session.async_payment_succeeded`

Copy its signing secret (`whsec_...`) into SSM:

```sh
read -rs V && aws ssm put-parameter --region us-west-2 --type SecureString \
  --name /fhir-scorecard/compliance-bundle/stripe-webhook-secret --value "$V"; unset V
```

## 7. Repository variables and the role secret

```sh
TF="terraform -chdir=infra/compliance-bundle output -raw"
gh variable set ARTIFACTS_BUCKET -R ChelseaKR/fhir-scorecard --body "$($TF artifacts_bucket)"
gh variable set BUNDLE_API_BASE  -R ChelseaKR/fhir-scorecard --body "$($TF api_base)"
gh variable set SES_FROM         -R ChelseaKR/fhir-scorecard --body "reports@chelseakr.com"
gh variable set AWS_REGION       -R ChelseaKR/fhir-scorecard --body "us-west-2"
gh secret   set AWS_ROLE_ARN     -R ChelseaKR/fhir-scorecard --body "$($TF fulfillment_role_arn)"
```

`reports@chelseakr.com` sends through the SES identity `chelseakr.com`, already verified and out
of the sandbox in this account. The delivery email says "reply to this email", so make
`reports@` an alias of a mailbox you read in Google Workspace, or change the address in both
`SES_FROM` and `ses_from_address` in `terraform.tfvars`.

## 8. Open the setup route in test mode

1. In `infra/compliance-bundle/terraform.tfvars` set `payments_enabled = "1"`, then
   `terraform -chdir=infra/compliance-bundle apply`. The plan fails if any of the three SSM
   parameters is missing: that is the gate working.
2. In `data/bundle/plan.json` set `"setup_api_base"` to the `api_base` output. Leave
   `"paymentsAvailable": false`: the public page stays closed, but the setup form can now be
   sent, which the test purchase needs. Commit on a branch, open a PR, merge on green, then
   redeploy (a merge to main does not):

   ```sh
   gh workflow run pages.yml -R ChelseaKR/fhir-scorecard --ref main
   ```

## 9. Test-mode purchase, end to end

1. Open the `bundle_15` test Payment Link (its `checkout_url` in `plan.json`). Pay with
   `4242 4242 4242 4242`, any future expiry, any CVC, and your own email.
2. Stripe returns you to `/bundle/setup/?session_id=cs_test_...`. Send the form with two real
   endpoint ids, one made-up id, and your email.
3. Expect "Thank you. Your reports are being generated..." Then:
   - `gh run list -R ChelseaKR/fhir-scorecard --workflow compliance-bundle.yml` shows one
     successful run. Open its log: your email, organization name, and endpoint ids must not
     appear anywhere (they are masked, and never inputs).
   - The email arrives from `reports@chelseakr.com`. The link downloads a zip whose manifest
     lists all three ids, the made-up one as "not a tracked registry id".
   - The Stripe webhook page shows the event delivered with a 200.
4. The negative checks: send the same form again (expect "This checkout already produced a
   bundle"); buy a second `bundle_15` and send 16 ids (expect a refusal naming the 15 limit,
   before the checkout is used).

## 10. Live mode

1. Finish the account's activation (business details, bank account, identity).
2. Switch the Dashboard to live mode and repeat section 2 there: a live `fhir-setup-temp` key,
   `scripts/stripe-setup.sh` (it now writes `stripe_mode = "live"`, live price ids, and live
   Payment Links), delete the temporary key, and create a live `fhir-bundle-lambda` key.
3. Replace the two Stripe secrets with the live ones (`--overwrite`), after adding a **live**
   webhook endpoint at the same URL with the same two events:

   ```sh
   read -rs V && aws ssm put-parameter --region us-west-2 --type SecureString --overwrite \
     --name /fhir-scorecard/compliance-bundle/stripe-restricted-key --value "$V"; unset V
   read -rs V && aws ssm put-parameter --region us-west-2 --type SecureString --overwrite \
     --name /fhir-scorecard/compliance-bundle/stripe-webhook-secret --value "$V"; unset V
   ```

   Until all of this and the apply below are done the setup route stays closed on its own: a
   test key under `stripe_mode = "live"` (or the reverse) is refused.
4. The script reset `payments_enabled` to `"0"`. Set it back to `"1"` and
   `terraform -chdir=infra/compliance-bundle apply`.

## 11. Publish the prices

In `data/bundle/plan.json` set `"paymentsAvailable": true` (the live `checkout_url`s and
`setup_api_base` are already there). Open a PR: CI refuses an open tier that still links to a
test-mode Payment Link or leaves a plan unsold (`tests/test_compliance_bundle_contract.py`).
Merge on green, then `gh workflow run pages.yml -R ChelseaKR/fhir-scorecard --ref main`, and
check <https://fhir.chelseakr.com/bundle/> shows both prices and two Buy links.

## 12. One live purchase, then refund it

Buy `bundle_15` with your own card and walk section 9 once more. Then refund it from the Dashboard
(Payments > the payment > Refund). Stripe keeps its processing fee on a refunded card payment,
so this costs about $7.52 (2.9% + 30 cents of $249). It is the only proof that the live key, the
live webhook, and the live Payment Links agree.

## 13. GA4 (property 554880958)

- **Admin > Events**: once each has been received, mark `view_item` and `begin_checkout` as key
  events. `purchase` is a key event by default. (Or create all three by name under **Admin > Key
  events > New key event** before they arrive.)
- **Admin > Data streams > the web stream > Enhanced measurement**: turn off **Form
  interactions**. It adds nothing here and the setup form is the one form a buyer fills.

## Afterward

- Refunds are by hand from the Dashboard. The promise is a full refund on request within 30 days
  and whenever an archive has not arrived within two business days of payment.
- A failed fulfillment run emails you through GitHub's failed-workflow notification. The repair
  is `gh workflow run compliance-bundle.yml -R ChelseaKR/fhir-scorecard --ref main -f
  order_ref=<the order_ref in the failed run's summary>`.
- To close the tier: `paymentsAvailable: false` in `plan.json` (PR, then redeploy pages), and
  `payments_enabled = "0"` plus an apply.
- Read the numbers at day 90 (see "The day-90 gate" in `docs/compliance-bundle-plan.md`).

## What this costs to run

The AWS side is pay-per-use with nothing idle to pay for: two Lambdas, an HTTP API, two
on-demand DynamoDB tables, one S3 bucket whose objects expire after 30 days, three standard SSM
parameters (free; Secrets Manager would have been $1.20 a month), and SES. Estimated **under
$0.10 a month at 0 to 10 orders, and under $1 at 100**; $0.00 idle. Stripe takes 2.9% + 30 cents
per card payment ($7.52 on $249, $14.77 on $499), plus 0.5% if Stripe Tax calculates the tax.
