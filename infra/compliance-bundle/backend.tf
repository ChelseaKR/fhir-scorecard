# Remote state in S3. The owner creates this bucket once (docs/compliance-bundle-owner-steps.md,
# step "State bucket") before the first `terraform init`. No secret is ever in this state: the
# three secrets live in SSM and are only checked for existence, without decryption (main.tf). It
# is still remote rather than a local terraform.tfstate on one laptop, because losing it would
# orphan every resource the module created. No lock table, for the same single-operator reason
# as gtfs-scorecard's infra/program-bundle/backend.tf.
terraform {
  backend "s3" {
    bucket  = "fhir-scorecard-tfstate-ckr"
    key     = "compliance-bundle/terraform.tfstate"
    region  = "us-west-2"
    encrypt = true
  }
}
