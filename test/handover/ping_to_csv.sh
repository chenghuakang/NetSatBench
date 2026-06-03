#!/bin/bash

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    echo "Usage: $0 <target|default> <duration_seconds> [interval_seconds]" >&2
    exit 1
fi

TARGET="$1"
if [ "$TARGET" = "default" ]; then
    read -r _ TARGET < "./grd_config" || {
        echo "Failed to read target from ./grd_config" >&2
        exit 1
    }
fi

DURATION="$2"
INTERVAL="${3:-1}"
SIZE="64"
OUTPUT="ping_${TARGET}.csv"
RAW_OUTPUT="ping_${TARGET}.raw"

COUNT="$(awk -v duration="$DURATION" -v interval="$INTERVAL" 'BEGIN { print int(duration / interval) }')"

echo "timestamp,icmp_seq,ttl,rtt_ms" > "$OUTPUT"

ping -i "$INTERVAL" "$TARGET" -c "$COUNT" -s "$SIZE" | tee "$RAW_OUTPUT" | awk -v outfile="$OUTPUT" '
BEGIN {
    FS = " "
}
{
    "date +%Y-%m-%dT%H:%M:%S.%3N" | getline timestamp
    close("date +%Y-%m-%dT%H:%M:%S.%3N")

    if ($0 ~ /bytes from/) {
        split($0, a, "time=");
        split(a[2], b, " ");
        rtt = b[1];

        icmp_seq = "0"; ttl = "0";
        for(i=1; i<=NF; i++) {
            if($i ~ /icmp_seq=/) { split($i, s, "="); icmp_seq = s[2]; }
            if($i ~ /ttl=/) { split($i, t, "="); ttl = t[2]; }
        }
        print timestamp "," icmp_seq "," ttl "," rtt >> outfile
    } 
    else if ($0 ~ /icmp_seq=/) {
        icmp_seq = "0";
        for(i=1; i<=NF; i++) {
            if($i ~ /icmp_seq=/) { split($i, s, "="); icmp_seq = s[2]; }
        }
        print timestamp "," icmp_seq ",-1,TIMEOUT" >> outfile
    }
    fflush(outfile)
}
'
