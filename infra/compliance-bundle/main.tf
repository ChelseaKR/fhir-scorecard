# Compliance report bundle (docs/compliance-bundle-plan.md): every AWS resource the paid tier
# needs, in one module. Two small zip-package Lambdas behind one HTTP API Gateway, a private
# bucket for orders and archives, two DynamoDB tables, and the role the fulfillment workflow
# assumes through GitHub OIDC:
#
#   POST /setup                 the post-checkout form; confirms the Stripe session is paid,
#                               writes the order to the bucket, then dispatches
#                               .github/workflows/compliance-bundle.yml by reference
#   GET  /download/{bundle_id}  the capability link in the delivery email; 302 to a
#                               fifteen-minute presigned S3 URL
#   POST /webhook               Stripe events -> checkout notes and subscription state
#
# Status: WRITTEN AND PLANNED, NEVER APPLIED. `terraform plan` has been run against the account
# (docs/compliance-bundle-owner-steps.md records the resource count and the monthly cost
# estimate); nothing it describes exists until the owner runs the apply in that checklist.
#
# Secrets are not Terraform's business here. The Stripe restricted key, the webhook signing
# secret, and the GitHub dispatch token are SecureString parameters under var.ssm_prefix, which
# the owner creates with `aws ssm put-parameter` and the Lambdas read at run time
# (common.secret). None of them is a variable of this module, an environment variable of a
# function, or a value in this module's state. What Terraform does check, at plan time, is that
# all three *exist* before payments can open: see data.aws_ssm_parameter.required below, which
# reads them without decryption.
#
# Everything that can charge anyone sits behind `payments_enabled`, which defaults to "0". With
# it "0", the setup route answers 503 and builds nothing, whatever else is configured. With it
# "1", the preconditions on terraform_data.commercial_gate_guard fail the *plan* until every
# price id is set, and the SSM lookups fail it until every secret exists. The Lambda then checks
# again on every request (common.payments_enabled): no key, no token, or no bucket still means
# no checkout is accepted.
#
# API Gateway, not a Lambda function URL, for the same reason gtfs-scorecard's sibling modules
# use it: a function URL is a second, un-auditable ingress per Lambda.
#
# Build the deployment package before planning or applying, from the repository root:
#   scripts/build-lambda-package.sh infra/compliance-bundle
# It vendors this package as Linux x86_64 / CPython 3.12 wheels and refuses a package holding a
# binary built for anything else. State lives in S3 (backend.tf).

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.0" }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = var.project
      component  = "compliance-bundle"
      managed-by = "terraform"
    }
  }
}

# ---------------------------------------------------------------------------
# Inputs. None of these is a secret.
# ---------------------------------------------------------------------------

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
  description = "owner/name the setup route dispatches compliance-bundle.yml against, and the only repository whose main branch may assume the fulfillment role."
}

variable "allow_origin" {
  type        = string
  default     = "https://fhir.chelseakr.com"
  description = "CORS origin of the setup form. Never '*' for a route that starts a paid build."
}

variable "payments_enabled" {
  type        = string
  default     = "0"
  description = "\"1\" opens the setup route; \"0\" (default) keeps every route that could accept a purchase closed. A string, validated exactly, so a tfvars typo fails the plan instead of silently disabling a launch that looks enabled."

  validation {
    condition     = contains(["0", "1"], var.payments_enabled)
    error_message = "payments_enabled must be exactly \"0\" or \"1\"."
  }
}

variable "stripe_mode" {
  type        = string
  default     = "test"
  description = "Which Stripe mode the price ids below and the restricted key in SSM belong to. The Lambdas refuse a key whose prefix is not rk_<mode>_ and ignore webhook events from the other mode. scripts/stripe-setup.sh prints this line next to the price ids it created, so the two are written together."

  validation {
    condition     = contains(["test", "live"], var.stripe_mode)
    error_message = "stripe_mode must be \"test\" or \"live\"."
  }
}

variable "stripe_price_ids" {
  type = map(string)
  default = {
    bundle_15 = ""
    bundle_70 = ""
  }
  description = "Stripe price id for each plan sold, from scripts/stripe-setup.sh. Only the two one-time plans: the quarterly refresh plans are not sold until a recurring re-dispatch exists (docs/compliance-bundle-plan.md), so this map cannot carry a price id for either."

  # A tfvars map replaces the default outright, so a missing or misspelled key would otherwise
  # pass the gate and refuse every purchase of that plan -- and a refresh key would sell a
  # subscription nothing delivers after its first quarter.
  validation {
    condition     = toset(keys(var.stripe_price_ids)) == toset(["bundle_15", "bundle_70"])
    error_message = "stripe_price_ids must have exactly the keys bundle_15 and bundle_70. The refresh plans are not sold yet."
  }

  # One price id on two plans would let the Lambdas guess which cap applies.
  validation {
    condition     = length(distinct(compact(values(var.stripe_price_ids)))) == length(compact(values(var.stripe_price_ids)))
    error_message = "stripe_price_ids has the same price id on more than one plan."
  }

  validation {
    condition     = alltrue([for id in values(var.stripe_price_ids) : id == "" || can(regex("^price_[A-Za-z0-9]+$", id))])
    error_message = "every stripe_price_ids value must be blank or a Stripe price id (price_...)."
  }
}

variable "ssm_prefix" {
  type        = string
  default     = "/fhir-scorecard/compliance-bundle"
  description = "SSM Parameter Store path holding the three SecureString secrets (stripe-restricted-key, stripe-webhook-secret, github-dispatch-token)."

  validation {
    condition     = can(regex("^/[a-z0-9-]+(/[a-z0-9-]+)*$", var.ssm_prefix))
    error_message = "ssm_prefix must be an absolute path such as /fhir-scorecard/compliance-bundle, with no trailing slash."
  }
}

variable "ses_identity" {
  type        = string
  default     = "chelseakr.com"
  description = "An SES identity already verified in this account and region. The fulfillment role may send only from ses_from_address, on this identity."
}

variable "ses_from_address" {
  type        = string
  default     = "reports@chelseakr.com"
  description = "The From address of the delivery email. Must sit on ses_identity; set the same value as the SES_FROM Actions variable."
}

# ---------------------------------------------------------------------------
# What already exists in the account, read rather than created.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

# The account holds exactly one GitHub OIDC provider already (gtfs-scorecard's
# infra/artifacts/github_oidc.tf created it); a second one for the same URL is refused by IAM.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

locals {
  account_id        = data.aws_caller_identity.current.account_id
  artifacts_bucket  = "${var.project}-compliance-bundles-${local.account_id}"
  price_ids_missing = [for k, v in var.stripe_price_ids : k if v == ""]
  secret_names      = ["stripe-restricted-key", "stripe-webhook-secret", "github-dispatch-token"]
  secret_arns       = [for name in local.secret_names : "arn:aws:ssm:${var.region}:${local.account_id}:parameter${var.ssm_prefix}/${name}"]
  ses_identity_arn  = "arn:aws:ses:${var.region}:${local.account_id}:identity/${var.ses_identity}"
}

# ---------------------------------------------------------------------------
# The gate. Preconditions and lookups, not check blocks: a check block only warns.
# ---------------------------------------------------------------------------

resource "terraform_data" "commercial_gate_guard" {
  input = {
    payments_enabled = var.payments_enabled
    stripe_mode      = var.stripe_mode
  }

  lifecycle {
    precondition {
      condition     = var.payments_enabled == "0" || length(local.price_ids_missing) == 0
      error_message = "payments_enabled is \"1\" but these price ids are blank: ${join(", ", local.price_ids_missing)}."
    }
    precondition {
      condition     = endswith(var.ses_from_address, "@${var.ses_identity}")
      error_message = "ses_from_address must be an address on ses_identity (${var.ses_identity})."
    }
  }
}

# Existence, not value: with_decryption = false keeps plaintext out of state (the value
# attribute holds only the KMS ciphertext). A parameter that does not exist fails the plan with
# ParameterNotFound, which is the point: payments cannot be opened ahead of the secrets.
data "aws_ssm_parameter" "required" {
  for_each        = var.payments_enabled == "1" ? toset(local.secret_names) : toset([])
  name            = "${var.ssm_prefix}/${each.value}"
  with_decryption = false
}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

# Orders in (compliance-requests/<ref>.json, written by the setup Lambda) and archives out
# (compliance-bundles/<id>/bundle.zip, written by the workflow). Private, encrypted, and
# short-lived: both prefixes expire after 30 days, in step with bundle.DOWNLOAD_DAYS and the
# DynamoDB TTL on capability rows. The only way to an archive is the download route, which
# presigns per click.
resource "aws_s3_bucket" "artifacts" {
  bucket = local.artifacts_bucket
}

resource "aws_s3_bucket_ownership_controls" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-compliance-bundles"
    status = "Enabled"
    filter {
      prefix = "compliance-bundles/"
    }
    expiration {
      days = 30
    }
  }

  # An order carries the buyer's email address and organization name. It is kept long enough
  # to re-run a failed build with the same reference, and no longer.
  rule {
    id     = "expire-compliance-requests"
    status = "Enabled"
    filter {
      prefix = "compliance-requests/"
    }
    expiration {
      days = 30
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.artifacts]
}

# Subscriptions: one row per Stripe subscription. Dormant until the refresh plans are sold;
# created now so turning them on is a code change, not a migration.
resource "aws_dynamodb_table" "subscriptions" {
  name         = "${var.project}-compliance-bundle-subscriptions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
}

# Capability rows (bare bundle id, 30-day TTL) plus the `session#` claims that make the setup
# route idempotent and the `checkout#` notes the webhook writes. Neither prefixed row carries
# `expires_at`: a claim has to outlive the capability, or a replay after 30 days would build a
# second bundle from one payment. Point-in-time recovery because losing a claim is losing that
# guarantee.
resource "aws_dynamodb_table" "bundles" {
  name         = "${var.project}-compliance-bundle-bundles"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "bundle_id"

  attribute {
    name = "bundle_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }
}

# ---------------------------------------------------------------------------
# Lambdas (one package, two entrypoints)
# ---------------------------------------------------------------------------

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
        Sid      = "Tables"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Scan"]
        Resource = [aws_dynamodb_table.subscriptions.arn, aws_dynamodb_table.bundles.arn]
      },
      {
        Sid      = "ReadArchives"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.artifacts.arn}/compliance-bundles/*"
      },
      {
        Sid      = "WriteOrders"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.artifacts.arn}/compliance-requests/*"
      },
      {
        # SecureString parameters encrypted with the AWS managed key aws/ssm: the key policy
        # already lets any principal in the account decrypt through SSM, so no kms: grant.
        Sid      = "ReadSecrets"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = local.secret_arns
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "setup" {
  name              = "/aws/lambda/${var.project}-compliance-bundle-setup"
  retention_in_days = 90
}

resource "aws_cloudwatch_log_group" "webhook" {
  name              = "/aws/lambda/${var.project}-compliance-bundle-webhook"
  retention_in_days = 90
}

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/build"
  output_path = "${path.module}/compliance-bundle.zip"
}

locals {
  lambda_environment = {
    SSM_PREFIX          = var.ssm_prefix
    STRIPE_MODE         = var.stripe_mode
    STRIPE_PRICE_IDS    = jsonencode(var.stripe_price_ids)
    PAYMENTS_ENABLED    = var.payments_enabled
    SUBSCRIPTIONS_TABLE = aws_dynamodb_table.subscriptions.name
    BUNDLES_TABLE       = aws_dynamodb_table.bundles.name
    ARTIFACTS_BUCKET    = aws_s3_bucket.artifacts.bucket
    ALLOW_ORIGIN        = var.allow_origin
  }
}

resource "aws_lambda_function" "setup" {
  function_name    = "${var.project}-compliance-bundle-setup"
  role             = aws_iam_role.lambda.arn
  handler          = "setup_handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = merge(local.lambda_environment, {
      GITHUB_REPO   = var.github_repo
      WORKFLOW_FILE = "compliance-bundle.yml"
      WORKFLOW_REF  = "main"
    })
  }

  depends_on = [
    aws_cloudwatch_log_group.setup,
    terraform_data.commercial_gate_guard,
    data.aws_ssm_parameter.required,
  ]
}

resource "aws_lambda_function" "webhook" {
  function_name    = "${var.project}-compliance-bundle-webhook"
  role             = aws_iam_role.lambda.arn
  handler          = "webhook_handler.handler"
  runtime          = "python3.12"
  timeout          = 15
  memory_size      = 256
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = local.lambda_environment
  }

  depends_on = [aws_cloudwatch_log_group.webhook, terraform_data.commercial_gate_guard]
}

# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

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

# Throttled well above what a passive, low-volume tier sees and well below what would run up a
# bill: every route is public, and the setup route calls Stripe and GitHub on each request.
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
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

# ---------------------------------------------------------------------------
# The fulfillment workflow's role (GitHub OIDC)
# ---------------------------------------------------------------------------

# Only a run on this repository's main branch can assume it. compliance-bundle.yml is dispatched
# on main (WORKFLOW_REF above), and a run dispatched on any other ref fails at the credentials
# step rather than reaching a buyer's order.
resource "aws_iam_role" "fulfillment" {
  name = "${var.project}-compliance-bundle-fulfillment"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:refs/heads/main"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "fulfillment" {
  name = "${var.project}-compliance-bundle-fulfillment"
  role = aws_iam_role.fulfillment.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadOrders"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.artifacts.arn}/compliance-requests/*"
      },
      {
        Sid      = "WriteArchives"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.artifacts.arn}/compliance-bundles/*"
      },
      {
        Sid       = "SendTheDownloadLink"
        Effect    = "Allow"
        Action    = ["ses:SendEmail"]
        Resource  = local.ses_identity_arn
        Condition = { StringEquals = { "ses:FromAddress" = var.ses_from_address } }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Outputs: every value the owner checklist asks to be copied somewhere
# ---------------------------------------------------------------------------

output "api_base" {
  value       = aws_apigatewayv2_api.api.api_endpoint
  description = "data/bundle/plan.json setup_api_base, and the Actions variable BUNDLE_API_BASE"
}

output "webhook_url" {
  value       = "${aws_apigatewayv2_api.api.api_endpoint}/webhook"
  description = "the Stripe webhook endpoint URL"
}

output "artifacts_bucket" {
  value       = aws_s3_bucket.artifacts.bucket
  description = "the Actions variable ARTIFACTS_BUCKET"
}

output "fulfillment_role_arn" {
  value       = aws_iam_role.fulfillment.arn
  description = "the Actions secret AWS_ROLE_ARN"
}

output "ssm_parameter_names" {
  value       = [for name in local.secret_names : "${var.ssm_prefix}/${name}"]
  description = "the three SecureString parameters the owner creates"
}
