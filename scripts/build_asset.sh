#!/usr/bin/env bash
# Build the Lambda deployment asset: the app plus vendored Linux wheels.
#
# =============================================================================
# WHY THERE IS NO DOCKER HERE
# =============================================================================
# This started life as a container image, on the belief that `lxml` — a C
# extension, and the thing that makes DTD validation possible at all — was
# painful to get into a zip bundle. That belief was wrong.
#
# `pip install --platform manylinux2014_aarch64 --only-binary=:all:` downloads
# the prebuilt Linux ARM binary directly onto a developer's machine, whatever
# that machine is. No Docker, no emulation, no cross-build. It is the same
# vendored-wheels pattern Xenia already uses for its own procurement Lambda.
#
# The container cost us a 2GB dependency, an ECR repository, slower deploys and
# slower cold starts, and bought nothing. A zip is strictly better here.
#
# =============================================================================
# WHY --only-binary=:all: IS LOAD-BEARING
# =============================================================================
# Without it, pip will happily fall back to building a source distribution
# using the LOCAL toolchain — producing a macOS binary that imports fine on the
# developer's laptop and fails at runtime in Lambda with an opaque
# "invalid ELF header". Forcing binary-only turns that silent, late failure
# into an immediate, obvious one at build time.
#
# The same applies to --platform and --python-version: they must be pinned to
# the Lambda runtime, not inherited from whatever Python happens to be on PATH.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
BUILD="$ROOT/infra/build/sandbox"

# Must match infra/sandbox/site_stack.py. Changing one without the other gives
# you a Lambda that cannot import its own dependencies.
PLATFORM="manylinux2014_aarch64"
PYTHON_VERSION="312"

echo "==> cleaning $BUILD"
rm -rf "$BUILD"
mkdir -p "$BUILD"

echo "==> vendoring dependencies ($PLATFORM, py$PYTHON_VERSION)"
# boto3 is deliberately NOT vendored: the Lambda runtime already ships it, and
# bundling a second copy adds ~15MB and risks diverging from the version AWS
# actually runs.
python3 -m pip install \
  --platform "$PLATFORM" \
  --python-version "$PYTHON_VERSION" \
  --only-binary=:all: \
  --target "$BUILD" \
  --quiet \
  -r "$ROOT/requirements-lambda.txt"

echo "==> copying application"
cp -R "$ROOT/app" "$BUILD/app"

# The reference pages are served from these files at runtime (app/reference.py),
# so they are part of the application, not documentation that happens to sit
# nearby. Copied under app/ rather than beside it so one path resolves both
# locally and in the bundle.
mkdir -p "$BUILD/app/reference_docs"
cp "$ROOT/docs/reference/"*.md "$BUILD/app/reference_docs/"

# Strip caches and test material. These are pure weight in a Lambda bundle and
# __pycache__ in particular can carry host-architecture artefacts.
find "$BUILD" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -type d -name "tests" -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -type f -name "*.pyc" -delete 2>/dev/null || true

echo
echo "==> verifying the bundle is actually Linux ARM"
# A macOS build of lxml imports perfectly on the laptop and dies in Lambda, so
# check the compiled objects rather than trusting pip.
if ! ls "$BUILD"/lxml/*.so >/dev/null 2>&1; then
  echo "FAIL: no compiled lxml objects in the bundle — DTD validation would" >&2
  echo "      break at runtime, which is the entire product." >&2
  exit 1
fi
if ! ls "$BUILD"/lxml/*aarch64-linux-gnu.so >/dev/null 2>&1; then
  echo "FAIL: lxml objects are not aarch64 Linux. Found:" >&2
  ls "$BUILD"/lxml/*.so | head -3 >&2
  exit 1
fi

# The DTDs are the validator's ground truth; a bundle without them starts and
# then fails on the first document.
if ! ls "$BUILD"/app/cxml/dtd/*.dtd >/dev/null 2>&1; then
  echo "FAIL: vendored cXML DTDs missing from the bundle." >&2
  exit 1
fi

SIZE="$(du -sh "$BUILD" | cut -f1)"
DTDS="$(ls "$BUILD"/app/cxml/dtd/*.dtd | wc -l | tr -d ' ')"
echo "    lxml: $(ls "$BUILD"/lxml/*aarch64-linux-gnu.so | wc -l | tr -d ' ') aarch64 objects"
echo "    DTDs: $DTDS"
echo "    size: $SIZE (Lambda unzipped limit is 250MB)"
echo
echo "==> asset ready at infra/build/sandbox"
