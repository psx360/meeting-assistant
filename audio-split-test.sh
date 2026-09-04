#!/bin/bash
set -o pipefail

mic_gain="${MIC_GAIN:-4}"
meeting_config="$HOME/.config/meeting-upload.env"
display_state="/run/user/$(id -u)/meeting-display.json"
[[ -r "$meeting_config" ]] && source "$meeting_config"
if [[ -r /var/lib/meeting-recorder/settings.json ]]; then
  saved_gain="$(python3 -c 'import json; print(json.load(open("/var/lib/meeting-recorder/settings.json"))["gain"])' 2>/dev/null || true)"
  [[ "$saved_gain" =~ ^[0-9]+([.][0-9]+)?$ ]] && mic_gain="$saved_gain"
fi
meeting_id="$(date +%Y-%m-%d_%H-%M-%S)"
output_dir="$HOME/audio-split-test/$meeting_id"
mkdir -p "$output_dir"
touch "$output_dir/.recording"

join_url=""
if [[ -n "${MEETING_UPLOAD_URL:-}" && -n "${MEETING_API_TOKEN:-}" ]]; then
  join_payload="$(python3 - "$meeting_id" "$MEETING_API_TOKEN" <<'PY'
import base64, hashlib, hmac, sys
meeting_id, secret = sys.argv[1:]
signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), meeting_id.encode(), hashlib.sha256).digest()[:12]).decode().rstrip("=")
print(f"m_{meeting_id}_{signature}")
PY
)"
  join_url="${MEETING_UPLOAD_URL%/}/m/$join_payload"
fi

write_display_state() {
  local phase="$1" qr_until="$2" temporary="$display_state.tmp"
  python3 - "$temporary" "$display_state" "$meeting_id" "$join_url" "$phase" "$qr_until" <<'PY'
import json, os, sys
temporary, destination, meeting_id, join_url, phase, qr_until = sys.argv[1:]
with open(temporary, "w", encoding="utf-8") as output:
    json.dump({"meeting_id": meeting_id, "join_url": join_url, "phase": phase, "qr_until": int(qr_until)}, output)
os.replace(temporary, destination)
PY
}

finish_recording() {
  status=$?
  trap - EXIT
  rm -f "$output_dir/.recording"
  touch "$output_dir/.ready"
  [[ -n "$join_url" ]] && write_display_state stopped "$(( $(date +%s) + 300 ))"
  exit "$status"
}
trap finish_recording EXIT
[[ -n "$join_url" ]] && write_display_state recording 0

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
