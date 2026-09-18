#!/usr/bin/env bash
# Build the deployment package for infra/compliance-bundle (ported from gtfs-scorecard's
# scripts/build-lambda-package.sh).
#
# The Lambdas import this repository's own package (fhir_scorecard.bundle, fhir_scorecard.deadline)
# next to the handler files. The package has no runtime dependencies today, but the Lambda
# runtime is Linux, so the install is still pinned to the runtime's platform and the result is
# still checked byte by byte: a macOS binary in a zip deploys cleanly and then fails on the first
# import, and nothing catches that before a paying buyer's request does.
#
# Usage (from the repository root):
#   scripts/build-lambda-package.sh infra/compliance-bundle
#
# It writes <module>/build/ and nothing else. It never touches Terraform, AWS, or the network
# beyond PyPI.

set -euo pipefail

# The Lambda runtime main.tf declares (`runtime = "python3.12"`, and no `architectures`, which is
# x86_64). Change these together with main.tf.
PLATFORM="manylinux2014_x86_64"
PYTHON_VERSION="3.12"
ELF_MAGIC="7f454c46"

usage() {
  echo "usage: scripts/build-lambda-package.sh infra/compliance-bundle" >&2
  exit 2
}

[ "$#" -eq 1 ] || usage
MODULE="${1%/}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODULE_DIR="$REPO_ROOT/$MODULE"

[ -d "$MODULE_DIR" ] || { echo "no such module directory: $MODULE_DIR" >&2; exit 2; }
[ -f "$MODULE_DIR/main.tf" ] || { echo "$MODULE is not a Terraform module (no main.tf)" >&2; exit 2; }

HANDLERS=("$MODULE_DIR"/*.py)
[ -e "${HANDLERS[0]}" ] || { echo "$MODULE has no handler .py files to package" >&2; exit 2; }

BUILD="$MODULE_DIR/build"
echo "== $MODULE: building $BUILD for $PLATFORM / CPython $PYTHON_VERSION"
rm -rf "$BUILD"

# --only-binary=:all: is what makes --platform meaningful for any dependency: pip may not build
# one from source here, because a source build would target this machine. The project itself is
# pure Python and is built from this checkout.
python3 -m pip install "$REPO_ROOT" -t "$BUILD" \
  --platform "$PLATFORM" \
  --python-version "$PYTHON_VERSION" \
  --implementation cp \
  --only-binary=:all: \
  --quiet

cp "${HANDLERS[@]}" "$BUILD/"

# The handlers import `fhir_scorecard`; a package that lacks it deploys and then fails on import.
[ -f "$BUILD/fhir_scorecard/bundle.py" ] || {
  echo "refusing this package: fhir_scorecard was not installed into $BUILD" >&2
  exit 1
}

foreign=0
while IFS= read -r so; do
  magic="$(od -An -tx1 -N4 "$so" | tr -d ' \n')"
  if [ "$magic" != "$ELF_MAGIC" ]; then
    echo "not a Linux (ELF) binary: ${so#"$BUILD"/} (magic $magic)" >&2
    foreign=$((foreign + 1))
  fi
done < <(find "$BUILD" -name '*.so' -type f)

if [ "$foreign" -ne 0 ]; then
  echo "refusing this package: $foreign compiled file(s) are not Linux builds." >&2
  echo "Nothing was deployed. Re-run this script; do not fall back to a plain pip install." >&2
  exit 1
fi

total_so="$(find "$BUILD" -name '*.so' -type f | wc -l | tr -d ' ')"
echo "== $MODULE: ok, $total_so compiled file(s), all Linux/ELF; handlers: $(basename -a "${HANDLERS[@]}" | tr '\n' ' ')"
