#!/bin/bash
set -o pipefail

output_dir="$HOME/audio-split-test/$(date +%Y-%m-%d_%H-%M-%S)"
mkdir -p "$output_dir"
touch "$output_dir/.recording"
trap 'rm -f "$output_dir/.recording"; touch "$output_dir/.ready"' EXIT

echo "AUDIO_TEST_START output=$output_dir gain=3x min_part=180s max_part=600s silence=1.2s detect=-40dB detect_delay=1.2s split=0.05% post_compand=on ceiling=-1dB"

ffmpeg \
  -nostdin \
  -nostats \
  -hide_banner \
  -loglevel info \
  -f pulse \
  -i alsa_input.platform-inmp441-sound.stereo-fallback \
  -af "volume=3,silencedetect=noise=-40dB:d=1.2" \
  -f s16le \
  -ar 48000 \
  -ac 2 \
  pipe:1 | \
sox \
  -V3 \
  -t raw \
  -r 48000 \
  -e signed-integer \
  -b 16 \
  -c 2 \
  -L \
  - \
  "$output_dir/part-%5n.flac" \
  trim 0 180 compand 0.02,0.25 6:-70,-58,-30,-18,-12,-6,0,-1 \
  : silence -l 0 1 1.2 0.05% trim 0 420 compand 0.02,0.25 6:-70,-58,-30,-18,-12,-6,0,-1 \
  : newfile \
  : restart

status=$?
echo "AUDIO_TEST_STOP status=$status output=$output_dir"
exit "$status"
