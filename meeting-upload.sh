#!/bin/bash
set -euo pipefail

source "$HOME/.config/meeting-upload.env"
base="$HOME/audio-split-test"
work="$HOME/.cache/meeting-upload"
mkdir -p "$work"

find "$base" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z | while IFS= read -r -d '' directory; do
    [[ -f "$directory/.ready" ]] || continue
    [[ ! -f "$directory/.uploaded" ]] || continue

    meeting_id="$(basename "$directory")"
    list="$work/$meeting_id.concat"
    audio="$work/$meeting_id.ogg"
    : > "$list"
    while IFS= read -r -d '' part; do
        printf "file '%s'\n" "$part" >> "$list"
    done < <(find "$directory" -maxdepth 1 -type f -name 'part-*.flac' -print0 | sort -z)
    [[ -s "$list" ]] || continue

    parts="$(wc -l < "$list")"
    echo "MEETING_MERGE_START id=$meeting_id parts=$parts"
    ffmpeg -nostdin -hide_banner -loglevel warning -y \
        -f concat -safe 0 -i "$list" \
        -vn -ac 1 -ar 16000 -c:a libopus -b:a 24k -application voip \
        "$audio"

    bytes="$(stat -c %s "$audio")"
    echo "MEETING_MERGE_FINISHED id=$meeting_id parts=$parts bytes=$bytes"
    if (( bytes > 25 * 1024 * 1024 )); then
        echo "MEETING_UPLOAD_FAILED id=$meeting_id reason=file-too-large bytes=$bytes" >&2
        exit 1
    fi

    echo "MEETING_UPLOAD_START id=$meeting_id bytes=$bytes"
    curl --fail --silent --show-error \
        --connect-timeout 20 --max-time 1800 \
        --retry 8 --retry-delay 15 --retry-all-errors \
        -H "Authorization: Bearer $MEETING_API_TOKEN" \
        -H "Content-Type: audio/ogg" \
        -H "X-Meeting-ID: $meeting_id" \
        --data-binary "@$audio" \
        "$MEETING_UPLOAD_URL/api/meeting/upload"
    echo
    touch "$directory/.uploaded"
    rm -f "$list" "$audio"
    echo "MEETING_UPLOAD_FINISHED id=$meeting_id"
done
