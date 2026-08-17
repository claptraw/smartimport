# Example only: replace the base image with the Beets image you already use.
FROM lscr.io/linuxserver/beets:latest

# Build from a checked-out SmartImport repository.
# `musicbrainzngs` is installed automatically from SmartImport's dependencies.
# The [notifications] extra also installs Apprise.
COPY . /tmp/beets-smartimport
RUN python -m pip install --no-cache-dir '/tmp/beets-smartimport[notifications]' \
    && rm -rf /tmp/beets-smartimport
