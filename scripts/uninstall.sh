#!/bin/bash
set -euo pipefail

# Undoes scripts/install.sh. The Omarchy plugin folder is left for
# `omarchy plugin remove`, since this script may be running from inside it.
# Your command packs in ~/.config/keyboard-coach are kept.

plugin_id="io.github.n0mahd.keyboard-coach"
bindings_path="$HOME/.config/hypr/bindings.lua"

systemctl --user disable --now keyboard-coach.service 2>/dev/null || true
rm -f -- "$HOME/.config/systemd/user/keyboard-coach.service"
systemctl --user daemon-reload

rm -f -- \
  "$HOME/.local/bin/keyboard-coach" \
  "$HOME/.local/bin/keyboard-coach-emit" \
  "$HOME/.local/bin/keyboard-coach-ingest" \
  "$HOME/.local/bin/keyboard-coach-harvest"
rm -rf -- "$HOME/.local/share/keyboard-coach" "$HOME/.local/state/keyboard-coach"

if [[ -f $bindings_path ]]; then
  sed -i '/^-- BEGIN keyboard-coach$/,/^-- END keyboard-coach$/d' "$bindings_path"
  hyprctl reload >/dev/null || true
fi

omarchy-shell -q shell setPluginEnabled "$plugin_id" false || true

printf 'Keyboard Coach uninstalled. Remove the banner panel with: omarchy plugin remove %s\n' "$plugin_id"
