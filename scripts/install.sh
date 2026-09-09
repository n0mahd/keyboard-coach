#!/bin/bash
set -euo pipefail

plugin_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
bin_path="$HOME/.local/bin/keyboard-coach"
shell_plugin_dir="$HOME/.config/omarchy/plugins/abdullah.keyboard-coach"
data_dir="$HOME/.local/share/keyboard-coach"
bindings_path="$HOME/.config/hypr/bindings.lua"
start_marker="-- BEGIN keyboard-coach"
end_marker="-- END keyboard-coach"
old_bin_path="$HOME/.local/bin/mouse-keyboard-coach"
old_shell_plugin_dir="$HOME/.config/omarchy/plugins/abdullah.mouse-keyboard-coach"
old_data_dir="$HOME/.local/share/mouse-keyboard-coach"

install -Dm755 "$plugin_root/scripts/coach.py" "$bin_path"
install -Dm644 "$plugin_root/omarchy-plugin/manifest.json" "$shell_plugin_dir/manifest.json"
install -Dm644 "$plugin_root/omarchy-plugin/Banner.qml" "$shell_plugin_dir/Banner.qml"
if [[ -f "$old_data_dir/catalog.json" && ! -f "$data_dir/catalog.json" ]]; then
  install -Dm644 "$old_data_dir/catalog.json" "$data_dir/catalog.json"
elif [[ ! -f "$data_dir/catalog.json" ]]; then
  install -Dm644 "$plugin_root/assets/catalog.json" "$data_dir/catalog.json"
fi
rm -f -- "$old_bin_path"
rm -rf -- "$old_shell_plugin_dir"

if command -v gsettings >/dev/null 2>&1; then
  gsettings set org.gnome.desktop.interface toolkit-accessibility true
fi

for flags_path in "$HOME/.config/brave-flags.conf" "$HOME/.config/chromium-flags.conf"; do
  if [[ -f $flags_path ]] && ! grep -Fxq -- '--force-renderer-accessibility' "$flags_path"; then
    printf '%s\n' '--force-renderer-accessibility' >> "$flags_path"
  fi
done

if ! grep -Fq -- "$start_marker" "$bindings_path"; then
  cp -- "$bindings_path" "$bindings_path.bak.keyboard-coach"
fi
sed -i \
  -e '/^-- BEGIN mouse-keyboard-coach$/,/^-- END mouse-keyboard-coach$/d' \
  -e '/^-- BEGIN keyboard-coach$/,/^-- END keyboard-coach$/d' \
  "$bindings_path"
printf '\n%s\n' "$start_marker" >> "$bindings_path"
sed -n '1,$p' "$plugin_root/scripts/hypr-bindings.lua" >> "$bindings_path"
printf '%s\n' "$end_marker" >> "$bindings_path"

omarchy-shell shell rescanPlugins
for _ in {1..30}; do
  if omarchy-shell shell listPlugins 2>/dev/null | grep -Fq 'abdullah.keyboard-coach'; then
    break
  fi
  sleep 0.1
done
omarchy-shell -q shell setPluginEnabled abdullah.mouse-keyboard-coach false || true
omarchy plugin enable abdullah.keyboard-coach
hyprctl reload
errors=$(hyprctl configerrors)
if [[ -n $errors && $errors != "no flags were set" && $errors != "-- No entries --" ]]; then
  printf '%s\n' "$errors" >&2
  exit 1
fi

printf 'Keyboard Coach installed and enabled.\n'
