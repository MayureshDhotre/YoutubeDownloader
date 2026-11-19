#!/usr/bin/env bash
# Launches the YouTube Downloader GUI on Linux.
cd "$(dirname "$(readlink -f "$0")")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found. Install it with: sudo apt install python3" >&2
    exit 1
fi

exec python3 yt_downloader.py "$@"
