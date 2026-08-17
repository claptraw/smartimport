#!/bin/sh
set -eu

# SmartImport itself does not assume a particular scheduler, container image,
# or Beets copy/move setting. Set this to the same staging path configured in
# smartimport.staging.
: "${SMARTIMPORT_STAGING:?Set SMARTIMPORT_STAGING to the configured staging path}"

beet smartimport

# Force MOVE for staged releases. This makes the automation independent of the
# user's global Beets defaults (which normally copy imported files).
find "$SMARTIMPORT_STAGING" -mindepth 1 -maxdepth 1 -type d \
  -exec sh -c '
    for release_dir do
      beet import -m "$release_dir" || true
    done
  ' sh {} +

# Anything Beets did not consume safely is routed to manual review.
beet smartcleanup
