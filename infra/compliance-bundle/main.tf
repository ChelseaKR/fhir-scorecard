# Compliance report bundle (docs/compliance-bundle-plan.md): the compliance-bundle tier's only
# always-on surface. Two small zip-package Lambdas behind one HTTP API Gateway:
#
#   POST /setup                 the post-checkout form; confirms the Stripe session is paid,
#                               then dispatches .github/workflows/compliance-bundle.yml
#   GET  /download/{bundle_id}  the capability link in the delivery email; 302 to a
#                               fifteen-minute presigned S3 URL
#   POST /webhook               Stripe events -> subscription state
#
# Deliberately NOT here, and why: a weekly/quarterly re-dispatch for active subscriptions and a
# daily reconciler auditing paid-but-undelivered orders both exist in this module's reference
# implementation (gtfs-scorecard's infra/program-bundle, refresh_handler.py and
# reconcile_handler.py) and are not ported in this first version. Until they exist, a quarterly
# refresh's *first* archive is dispatched by the setup route itself (setup_handler.py records
# this in a comment), but nothing re-sends the second one automatically, and an order that pays
# and never returns to the setup form is not audited anywhere but the DynamoDB tables directly.
# See docs/compliance-bundle-plan.md for the resume plan.
#
# Status: WRITTEN, NEVER APPLIED. No `terraform init`, `plan`, or `apply` has been run against
# this module, and no AWS account has been asked to create anything it describes. It is exactly
# as far as gtfs-scorecard's own infra/program-bundle went before its Stripe account existed
# (see that module's own header comment: "written, not yet applied"). Everything that can charge
# anyone sits behind `payments_enabled`, which defaults to "0" and cannot be turned on while the
# Stripe configuration is blank: the preconditions on terraform_data.commercial_gate_guard fail
# the *plan*, not a warning a CI `plan -out && apply` would never show anyone.
#
# API Gateway, not a Lambda function URL, for the same reason gtfs-scorecard's sibling modules
# use it: a function URL is a second, un-auditable ingress per Lambda, and this account (once one
# exists for this repo) should have one.
#
# Build the deployment package before applying, from the repository root:
#   scripts/build-lambda-package.sh infra/compliance-bundle
# (a script this repository does not have yet; port it from gtfs-scorecard's
# scripts/build-lambda-package.sh, which vendors the Python package as Linux x86_64 / CPython
# 3.12 wheels -- a plain `pip install . -t build` on a Mac vendors macOS binaries that fail to
# import in the Lambda runtime). State should live in S3, exactly as gtfs-scorecard's
# infra/program-bundle/backend.tf does, because it will hold the GitHub token and both Stripe
# secrets: never a local terraform.tfstate on one laptop.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.0" }
  }
}

locals {
  default_tags = {
    project    = var.project
    component  = "compliance-bundle"
    managed-by = "terraform"
  }
  price_ids_missing  = [for k, v in var.stripe_price_ids : k if v == ""]
  stripe_key_is_live = startswith(var.stripe_secret_key, "rk_live_") || startswith(var.stripe_secret_key, "sk_live_")
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.default_tags
  }
}

variable "project" {
  type    = string
  default = "fhir-scorecard"
}

variable "region" {
  type    = string
  default = "us-west-2"
}

variable "github_repo" {
  type        = string
  default     = "ChelseaKR/fhir-scorecard"
  description = "owner/name this module dispatches compliance-bundle.yml against"
}

variable "github_token" {
  type        = string
  sensitive   = true
  description = "fine-grained PAT with actions: write on github_repo and nothing else"
}

variable "artifacts_bucket" {
  type        = string
  description = "S3 bucket compliance-bundle.yml uploads compliance-bundles/<id>/bundle.zip to"
}

variable "allow_origin" {
  type    = string
  default = "https://fhir.chelseakr.com"
}

variable "payments_enabled" {
  type        = string
  default     = "0"
  description = "\"1\" opens the /setup route; anything else closes it. See commercial_gate_guard below."
}

variable "stripe_secret_key" {
  type      = string
  sensitive = true
  default   = ""
  validation {
    # A restricted key ("rk_") can read Checkout Sessions and nothing else. A full secret key
    # ("sk_") can move money and must never be handed to a deployed Lambda.
    condition     = var.stripe_secret_key == "" || can(regex("^rk_", var.stripe_secret_key))
    error_message = "stripe_secret_key must be a restricted key (rk_...), never a full secret key."
  }
}

variable "stripe_webhook_secret" {
  type      = string
  sensitive = true
  default   = ""
}

variable "stripe_price_ids" {
  type = map(string)
  default = {
    bundle_15   = ""
    bundle_70   = ""
    refresh_qtr = ""
    refresh_yr  = ""
  }
  description = "plan key -> Stripe price id, from scripts/stripe-setup.sh (not yet written for this repo)"
}

variable "stripe_price_ids_are_live" {
  type        = bool
  default     = false
  description = "must be explicitly set true before a live secret key is accepted alongside live prices"
}

# The same shape as gtfs-scorecard's terraform_data.commercial_gate_guard: a plan-time failure,
# not a runtime check, so payments cannot be opened by an `apply` nobody read closely. Every
# condition here has to hold before payments_enabled can be "1".
resource "terraform_data" "commercial_gate_guard" {
  lifecycle {
    precondition {
      condition     = var.payments_enabled != "1" || length(local.price_ids_missing) == 0
      error_message = "payments_enabled=1 requires every stripe_price_ids entry to be set; missing: ${join(", ", local.price_ids_missing)}"
    }
    precondition {
      condition     = var.payments_enabled != "1" || var.stripe_secret_key != ""
      error_message = "payments_enabled=1 requires stripe_secret_key"
    }
    precondition {
      condition     = var.payments_enabled != "1" || var.stripe_webhook_secret != ""
      error_message = "payments_enabled=1 requires stripe_webhook_secret"
    }
    precondition {
      condition     = !local.stripe_key_is_live || var.stripe_price_ids_are_live
      error_message = "a live Stripe key was supplied but stripe_price_ids_are_live is false; refusing to pair a live key with prices that might be test-mode"
    }
  }
}

resource "aws_dynamodb_table" "subscriptions" {
  name         = "${var.project}-compliance-bundle-subscriptions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "bundles" {
  name         = "${var.project}-compliance-bundle-bundles"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "bundle_id"
  # TTL only removes bare capability rows (an int `expires_at`); session#/checkout# rows never
  # carry one and are meant to outlive the 30-day capability window. See common.py.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  attribute {
    name = "bundle_id"
    type = "S"
  }
}

resource "aws_iam_role" "lambda" {
  name = "${var.project}-compliance-bundle-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda" {
  name = "${var.project}-compliance-bundle-access"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Scan"]
        Resource = [aws_dynamodb_table.subscriptions.arn, aws_dynamodb_table.bundles.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:HeadObject"]
        Resource = "arn:aws:s3:::${var.artifacts_bucket}/compliance-bundles/*"
      },
    ]
  })
}

# Deployment package: this repository does not have scripts/build-lambda-package.sh yet (see the
# header comment); until it does, `terraform apply` for this module cannot succeed, which is a
# feature of the current state, not a bug in this file.
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/build"
  output_path = "${path.module}/compliance-bundle.zip"
}

resource "aws_lambda_function" "setup" {
  function_name    = "${var.project}-compliance-bundle-setup"
  role             = aws_iam_role.lambda.arn
  handler          = "setup_handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      GITHUB_TOKEN        = var.github_token
      GITHUB_REPO         = var.github_repo
      WORKFLOW_FILE       = "compliance-bundle.yml"
      STRIPE_SECRET_KEY   = var.stripe_secret_key
      STRIPE_PRICE_IDS    = jsonencode(var.stripe_price_ids)
      PAYMENTS_ENABLED    = var.payments_enabled
      SUBSCRIPTIONS_TABLE = aws_dynamodb_table.subscriptions.name
      BUNDLES_TABLE       = aws_dynamodb_table.bundles.name
      ARTIFACTS_BUCKET    = var.artifacts_bucket
      ALLOW_ORIGIN        = var.allow_origin
    }
  }
}

resource "aws_lambda_function" "webhook" {
  function_name    = "${var.project}-compliance-bundle-webhook"
  role             = aws_iam_role.lambda.arn
  handler          = "webhook_handler.handler"
  runtime          = "python3.12"
  timeout          = 15
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      STRIPE_SECRET_KEY     = var.stripe_secret_key
      STRIPE_WEBHOOK_SECRET = var.stripe_webhook_secret
      STRIPE_PRICE_IDS      = jsonencode(var.stripe_price_ids)
      SUBSCRIPTIONS_TABLE   = aws_dynamodb_table.subscriptions.name
      BUNDLES_TABLE         = aws_dynamodb_table.bundles.name
      ALLOW_ORIGIN          = var.allow_origin
    }
  }
}

resource "aws_apigatewayv2_api" "api" {
  name          = "${var.project}-compliance-bundle"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "setup" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.setup.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "webhook" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.webhook.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "setup" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "POST /setup"
  target    = "integrations/${aws_apigatewayv2_integration.setup.id}"
}

resource "aws_apigatewayv2_route" "setup_options" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "OPTIONS /setup"
  target    = "integrations/${aws_apigatewayv2_integration.setup.id}"
}

resource "aws_apigatewayv2_route" "download" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /download/{bundle_id}"
  target    = "integrations/${aws_apigatewayv2_integration.setup.id}"
}

resource "aws_apigatewayv2_route" "webhook" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "POST /webhook"
  target    = "integrations/${aws_apigatewayv2_integration.webhook.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "setup" {
  statement_id  = "AllowAPIGatewayInvokeSetup"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.setup.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

resource "aws_lambda_permission" "webhook" {
  statement_id  = "AllowAPIGatewayInvokeWebhook"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.webhook.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

output "api_base" {
  value       = aws_apigatewayv2_api.api.api_endpoint
  description = "set as the Actions repository variable BUNDLE_API_BASE, and as window.FHIR_SCORECARD_BUNDLE_URL"
}

output "webhook_url" {
  value = "${aws_apigatewayv2_api.api.api_endpoint}/webhook"
}
