#!/bin/sh
set -eu

socket_path="${XDG_RUNTIME_DIR:-/tmp}/keyboard-coach/events.sock"
printf '%s %s\n' "${1:-}" "${2:-}" | socat -u - "UNIX-SENDTO:${socket_path}" 2>/dev/null || true
