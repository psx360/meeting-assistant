#!/bin/bash
set -u

for source in room-silence-x1.wav normal-speech-2-3m-x1.wav; do
  echo "SOURCE $source"
  for threshold in 0.05 0.1 0.2 0.3 0.5 0.7 1; do
    output="/tmp/sox-${source%.wav}-${threshold}.wav"
    rm -f "$output"
    sox "/home/radxa/$source" "$output" silence -l 0 1 1.2 "${threshold}%" 2>/dev/null
    duration=$(soxi -D "$output" 2>/dev/null || echo error)
    echo "threshold=${threshold}% duration=${duration}s"
  done
done
