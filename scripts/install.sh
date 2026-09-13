#!/bin/bash
set -euo pipefail

plugin_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
bin_path="$HOME/.local/bin/keyboard-coach"
emit_path="$HOME/.local/bin/keyboard-coach-emit"
ingest_path="$HOME/.local/bin/keyboard-coach-ingest"
shell_plugin_dir="$HOME/.config/omarchy/plugins/abdullah.keyboard-coach"
data_dir="$HOME/.local/share/keyboard-coach"
config_dir="$HOME/.config/keyboard-coach"
state_dir="$HOME/.local/state/keyboard-coach"
service_path="$HOME/.config/systemd/user/keyboard-coach.service"
bindings_path="$HOME/.config/hypr/bindings.lua"
start_marker="-- BEGIN keyboard-coach"
end_marker="-- END keyboard-coach"

old_bin_path="$HOME/.local/bin/mouse-keyboard-coach"
old_emit_path="$HOME/.local/bin/mouse-keyboard-coach-emit"
old_ingest_path="$HOME/.local/bin/mouse-keyboard-coach-ingest"
old_shell_plugin_dir="$HOME/.config/omarchy/plugins/abdullah.mouse-keyboard-coach"
old_data_dir="$HOME/.local/share/mouse-keyboard-coach"
old_config_dir="$HOME/.config/mouse-keyboard-coach"
old_state_dir="$HOME/.local/state/mouse-keyboard-coach"
old_service_path="$HOME/.config/systemd/user/mouse-keyboard-coach.service"

if ! command -v socat >/dev/null 2>&1; then
  printf 'Keyboard Coach requires socat for lightweight event delivery.\n' >&2
  exit 1
fi

# Preserve existing user data on the first renamed install. The legacy trees are
# intentionally retained as a rollback copy and are never read after migration.
for migration in \
  "$old_data_dir:$data_dir" \
  "$old_config_dir:$config_dir" \
  "$old_state_dir:$state_dir"
do
  old_path=${migration%%:*}
  new_path=${migration#*:}
  if [[ -d $old_path && ! -e $new_path ]]; then
    mkdir -p -- "$(dirname -- "$new_path")"
    cp -a -- "$old_path" "$new_path"
  fi
done

install -Dm755 "$plugin_root/scripts/coach.py" "$bin_path"
install -Dm755 "$plugin_root/scripts/emit.sh" "$emit_path"
install -Dm755 "$plugin_root/scripts/ingest_catalog.py" "$ingest_path"
install -Dm644 "$plugin_root/systemd/keyboard-coach.service" "$service_path"
install -Dm644 "$plugin_root/omarchy-plugin/manifest.json" "$shell_plugin_dir/manifest.json"
install -Dm644 "$plugin_root/omarchy-plugin/Banner.qml" "$shell_plugin_dir/Banner.qml"
install -Dm644 "$plugin_root/assets/base-catalog.json" "$data_dir/base-catalog.json"
for command_pack in "$plugin_root"/assets/commands/*.json; do
  install -Dm644 "$command_pack" "$data_dir/commands/$(basename "$command_pack")"
done
"$ingest_path"

systemctl --user disable --now mouse-keyboard-coach.service 2>/dev/null || true
rm -f -- "$old_service_path"
systemctl --user daemon-reload
systemctl --user enable keyboard-coach.service
systemctl --user restart keyboard-coach.service

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

omarchy-shell -q shell setPluginEnabled abdullah.mouse-keyboard-coach false || true
rm -rf -- "$old_shell_plugin_dir"
omarchy-shell shell rescanPlugins
for _ in {1..30}; do
  if omarchy-shell shell listPlugins 2>/dev/null | grep -Fq 'abdullah.keyboard-coach'; then
    break
  fi
  sleep 0.1
done
omarchy plugin enable abdullah.keyboard-coach
hyprctl reload
errors=$(hyprctl configerrors)
if [[ -n $errors && $errors != "no flags were set" && $errors != "-- No entries --" ]]; then
  printf '%s\n' "$errors" >&2
  exit 1
fi

rm -f -- "$old_bin_path" "$old_emit_path" "$old_ingest_path"

printf 'Keyboard Coach installed and enabled.\n'
