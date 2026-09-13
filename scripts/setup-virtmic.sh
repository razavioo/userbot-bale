#!/usr/bin/env bash
# Create a PipeWire/PulseAudio virtual microphone outside the Python process,
# so multiple tools can share it. Prints the two module IDs; save them to
# unload later with `pactl unload-module <id>`.
set -euo pipefail

NAME="${1:-userbot-bale}"
DESC="${2:-Userbot Bale Virtual Mic}"

if ! command -v pactl >/dev/null 2>&1; then
  echo "pactl not found. Install pulseaudio-utils." >&2
  exit 1
fi

SINK_ID=$(pactl load-module module-null-sink \
  sink_name="${NAME}_sink" \
  sink_properties="device.description='${DESC}'")

SOURCE_ID=$(pactl load-module module-remap-source \
  master="${NAME}_sink.monitor" \
  source_name="${NAME}" \
  source_properties="device.description='${DESC}'")

cat <<EOF
Virtual mic created.
  sink   (play here):    ${NAME}_sink         [module ${SINK_ID}]
  source (app mic):      ${NAME}              [module ${SOURCE_ID}]

To tear down:
  pactl unload-module ${SOURCE_ID}
  pactl unload-module ${SINK_ID}
EOF
