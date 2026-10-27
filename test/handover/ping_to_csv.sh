#!/bin/bash

TARGET="$1"
DURATION="$2"
INTERVAL="${3:-1}"
SIZE="64"

if [ "$TARGET" = "default" ]; then
    read -r _ TARGET < "./grd_config" || exit 1
fi

COUNT="$(awk -v duration="$DURATION" -v interval="$INTERVAL" 'BEGIN { print int(duration / interval) }')"

OUTPUT_RAW="ping_${TARGET}.raw"

rm -f "$OUTPUT_RAW"

ping -D -i "$INTERVAL" "$TARGET" -c "$COUNT" -s "$SIZE" > "$OUTPUT_RAW" 2>&1
