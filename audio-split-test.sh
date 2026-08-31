#!/bin/bash
set -o pipefail

mic_gain="${MIC_GAIN:-4}"
if [[ -r /var/lib/meeting-recorder/settings.json ]]; then
  saved_gain="$(python3 -c 'import json; print(json.load(open("/var/lib/meeting-recorder/settings.json"))["gain"])' 2>/dev/null || true)"
  [[ "$saved_gain" =~ ^[0-9]+([.][0-9]+)?$ ]] && mic_gain="$saved_gain"
fi
output_dir="$HOME/audio-split-test/$(date +%Y-%m-%d_%H-%M-%S)"
mkdir -p "$output_dir"
touch "$output_dir/.recording"
trap 'rm -f "$output_dir/.recording"; touch "$output_dir/.ready"' EXIT

echo "AUDIO_TEST_START output=$output_dir codec=opus bitrate=64k channels=2 gain=${mic_gain}x part=600s auto_stop=21600s detect=-40dB"

ffmpeg \
  -nostdin \
  -nostats \
  -hide_banner \
  -loglevel info \
  -f pulse \
  -i alsa_input.platform-inmp441-sound.stereo-fallback \
  -t 21600 \
  -af "volume=${mic_gain},alimiter=limit=0.891:attack=5:release=50,silencedetect=noise=-40dB:d=1.2" \
  -ar 48000 \
  -ac 2 \
  -c:a libopus \
  -b:a 64k \
  -vbr on \
  -application audio \
  -f segment \
  -segment_time 600 \
  -reset_timestamps 1 \
  "$output_dir/part-%05d.ogg"

status=$?
echo "AUDIO_TEST_STOP status=$status output=$output_dir"
exit "$status"
