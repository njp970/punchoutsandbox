#!/usr/bin/env bash
# Refresh the vendored cXML DTDs from cxml.org.
#
# Prints a diff against what is committed rather than overwriting silently:
# a DTD revision changes what "conformant" means, so it is a decision, not an
# update. See app/cxml/dtd/README.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DTD_DIR="$HERE/../app/cxml/dtd"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

URL="https://xml.cxml.org/current/cXML_DTDs.zip"

echo "fetching $URL"
curl -fsSL -o "$TMP/dtds.zip" "$URL"
unzip -q -j "$TMP/dtds.zip" '*.dtd' -d "$TMP/new"

echo
echo "version directory in archive:"
unzip -l "$TMP/dtds.zip" | awk '/cXML\/[0-9]/ {print "  " $4; exit}'

echo
changed=0
for f in "$TMP"/new/*.dtd; do
  base="$(basename "$f")"
  if [[ ! -f "$DTD_DIR/$base" ]]; then
    echo "NEW      $base"
    changed=1
  elif ! cmp -s "$f" "$DTD_DIR/$base"; then
    echo "CHANGED  $base"
    changed=1
  else
    echo "same     $base"
  fi
done

if [[ $changed -eq 0 ]]; then
  echo
  echo "Nothing changed. Vendored DTDs are current."
  exit 0
fi

echo
echo "Changes found. To accept them:"
echo "  cp $TMP/new/*.dtd $DTD_DIR/    # (this temp dir is deleted on exit — re-run and copy promptly)"
echo "  shasum -a 256 $DTD_DIR/*.dtd   # update the checksum block in the README"
echo
echo "Then re-run the conformance test suite BEFORE committing: a DTD revision"
echo "can change which documents we call valid, and that is the product."
exit 1
