#!/bin/bash
# measure.sh <label> [seconds]: samples CPU and power per process (top, 2 s steps) and the GPU's
# "Device Utilization %" (ioreg) while the game runs, then prints averages.
# Run it once with the recorder and once without, same scene, and compare.
set -euo pipefail
LABEL=${1:?label}; SECS=${2:-120}
OUT="$HOME/Library/Logs/SlamReplay"; mkdir -p "$OUT"
TOP="$OUT/top-$LABEL.txt"; GPU="$OUT/gpu-$LABEL.txt"
N=$(( SECS / 2 + 1 ))
( for _ in $(seq 1 $(( SECS / 2 ))); do
    ioreg -r -c IOAccelerator -d 1 -w 0 | grep -o '"Device Utilization %"=[0-9]*' | head -1 | cut -d= -f2
    sleep 2
  done ) > "$GPU" &
top -l "$N" -s 2 -o cpu -stats pid,command,cpu,mem,power -n 80 > "$TOP"
wait
echo "== $LABEL: ${SECS}s =="
# Skip top's first sample (CPU since process start); average the rest per command.
awk '/^PID/ {s++; next} s>=2 && NF>=5 {
       cmd=$2; for (i=3; i<=NF-3; i++) cmd=cmd" "$i
       if (cmd ~ /dota2|SlamReplay|WindowServer|replayd|VTEncoder|VTDecoder|Steam Helper|steam_osx/) {
         cpu[cmd]+=$(NF-2); pw[cmd]+=$NF; n[cmd]++ } }
     END { for (c in cpu) printf "  %-22s cpu %6.1f%%  power %6.1f  (%d samples)\n", c, cpu[c]/n[c], pw[c]/n[c], n[c] }' "$TOP" | sort
awk '{t+=$1; n++; if ($1>m) m=$1} END { if (n) printf "  GPU device utilization avg %.0f%%, max %d%% (%d samples)\n", t/n, m, n }' "$GPU"
