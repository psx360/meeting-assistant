#!/bin/bash
set -euo pipefail

source "$HOME/.config/meeting-upload.env"
base="$HOME/audio-split-test"
work="$HOME/.cache/meeting-upload"
progress_file="/run/user/$(id -u)/meeting-upload-progress.json"
mkdir -p "$work"

write_progress() {
    local phase="$1" percent="$2" meeting_id="$3"
    printf '{"phase":"%s","percent":%d,"meeting_id":"%s"}\n' "$phase" "$percent" "$meeting_id" > "$progress_file.tmp"
    mv "$progress_file.tmp" "$progress_file"
}

find "$base" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z | while IFS= read -r -d '' directory; do
    [[ -f "$directory/.ready" ]] || continue
    [[ ! -f "$directory/.uploaded" ]] || continue

    meeting_id="$(basename "$directory")"
    list="$work/$meeting_id.concat"
    audio="$work/$meeting_id.ogg"
    : > "$list"
    while IFS= read -r -d '' part; do
        printf "file '%s'\n" "$part" >> "$list"
    done < <(find "$directory" -maxdepth 1 -type f \( -name 'part-*.ogg' -o -name 'part-*.flac' \) -print0 | sort -z)
    [[ -s "$list" ]] || continue

    parts="$(wc -l < "$list")"
    echo "MEETING_MERGE_START id=$meeting_id parts=$parts"
    write_progress merge 0 "$meeting_id"
    first_part="$(sed -n "s/^file '\(.*\)'$/\1/p" "$list" | head -1)"
    if [[ "$first_part" == *.ogg ]]; then
        ffmpeg -nostdin -hide_banner -loglevel warning -y \
            -f concat -safe 0 -i "$list" -vn -c:a copy "$audio"
    else
        ffmpeg -nostdin -hide_banner -loglevel warning -y \
            -f concat -safe 0 -i "$list" \
            -vn -ac 1 -ar 16000 -c:a libopus -b:a 24k -application voip \
            "$audio"
    fi
    write_progress merge 100 "$meeting_id"

    bytes="$(stat -c %s "$audio")"
    echo "MEETING_MERGE_FINISHED id=$meeting_id parts=$parts bytes=$bytes"
    if (( bytes > 256 * 1024 * 1024 )); then
        echo "MEETING_UPLOAD_FAILED id=$meeting_id reason=file-too-large bytes=$bytes" >&2
        exit 1
    fi

    echo "MEETING_UPLOAD_START id=$meeting_id bytes=$bytes"
    "$HOME/meeting-upload-http.py" \
        "$MEETING_UPLOAD_URL/api/meeting/upload" "$audio" \
        "$MEETING_API_TOKEN" "$meeting_id" "$progress_file"
    touch "$directory/.uploaded"
    rm -f "$list" "$audio"
    echo "MEETING_UPLOAD_FINISHED id=$meeting_id"
done
