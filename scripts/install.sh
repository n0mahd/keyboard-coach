#!/bin/bash
set -euo pipefail

plugin_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
bin_path="$HOME/.local/bin/keyboard-coach"
emit_path="$HOME/.local/bin/keyboard-coach-emit"
ingest_path="$HOME/.local/bin/keyboard-coach-ingest"
harvest_path="$HOME/.local/bin/keyboard-coach-harvest"
plugin_id="io.github.n0mahd.keyboard-coach"
shell_plugin_dir="$HOME/.config/omarchy/plugins/$plugin_id"
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
old_shell_plugin_ids=(abdullah.mouse-keyboard-coach abdullah.keyboard-coach)
old_data_dir="$HOME/.local/share/mouse-keyboard-coach"
old_config_dir="$HOME/.config/mouse-keyboard-coach"
old_state_dir="$HOME/.local/state/mouse-keyboard-coach"
old_service_path="$HOME/.config/systemd/user/mouse-keyboard-coach.service"

if ! command -v socat >/dev/null 2>&1; then
  printf 'Keyboard Coach requires socat for lightweight event delivery.\n' >&2
  exit 1
fi

if ! command -v lua5.5 >/dev/null 2>&1 && ! command -v lua >/dev/null 2>&1; then
  printf 'Warning: no Lua interpreter found. Hyprland bindings will be indexed without their commands,\n' >&2
  printf 'so Omarchy widget shortcuts cannot be resolved. Install the lua package to fix this.\n' >&2
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

# The shortcut harvester is built from the Rust crate at the repository root.
if command -v cargo >/dev/null 2>&1; then
  cargo build --release --manifest-path "$plugin_root/Cargo.toml"
elif command -v mise >/dev/null 2>&1; then
  (cd "$plugin_root" && mise exec -- cargo build --release)
else
  printf 'Keyboard Coach requires a Rust toolchain (cargo) to build keyboard-coach-harvest.\n' >&2
  exit 1
fi
install -Dm755 "$plugin_root/target/release/keyboard-coach-harvest" "$harvest_path"
install -Dm644 "$plugin_root/systemd/keyboard-coach.service" "$service_path"
# `omarchy plugin add` clones this repository into the plugin directory; an
# install from any other checkout copies the banner panel there.
if [[ ! $plugin_root -ef $shell_plugin_dir ]]; then
  install -Dm644 "$plugin_root/manifest.json" "$shell_plugin_dir/manifest.json"
  install -Dm644 "$plugin_root/Banner.qml" "$shell_plugin_dir/Banner.qml"
fi
install -Dm644 "$plugin_root/assets/base-catalog.json" "$data_dir/base-catalog.json"
install -Dm644 "$plugin_root/scripts/hypr-bind-dump.lua" "$data_dir/hypr-bind-dump.lua"
# The shipped pack directory is owned by the plugin: packs renamed or dropped by
# an update must not linger and keep matching. User packs live in $config_dir.
rm -rf -- "$data_dir/commands"
for command_pack in "$plugin_root"/assets/commands/*.json; do
  install -Dm644 "$command_pack" "$data_dir/commands/$(basename "$command_pack")"
done
"$ingest_path"
"$harvest_path"

systemctl --user disable --now mouse-keyboard-coach.service 2>/dev/null || true
rm -f -- "$old_service_path"
systemctl --user daemon-reload
# Re-enable so an install that was wanted by default.target moves to the
# graphical session; the old link started the daemon before Hyprland existed.
systemctl --user disable keyboard-coach.service 2>/dev/null || true
systemctl --user enable keyboard-coach.service
systemctl --user restart keyboard-coach.service

if command -v gsettings >/dev/null 2>&1; then
  gsettings set org.gnome.desktop.interface toolkit-accessibility true
fi

for flags_path in "$HOME"/.config/*-flags.conf; do
  [[ -f $flags_path ]] || continue
  if ! grep -Fxq -- '--force-renderer-accessibility' "$flags_path"; then
    printf '%s\n' '--force-renderer-accessibility' >> "$flags_path"
  fi
done

# Qt publishes no accessibility tree unless it is asked to, which leaves every
# Qt and KDE application invisible to the coach. The variable reaches
# applications the session starts; it takes effect at the next login.
install -Dm644 /dev/stdin "$HOME/.config/environment.d/90-keyboard-coach.conf" <<'ENV'
# Written by Keyboard Coach so Qt and KDE applications publish their menus.
QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1
ENV

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

for old_id in "${old_shell_plugin_ids[@]}"; do
  omarchy-shell -q shell setPluginEnabled "$old_id" false || true
  rm -rf -- "$HOME/.config/omarchy/plugins/$old_id"
done
omarchy-shell shell rescanPlugins
for _ in {1..30}; do
  if omarchy-shell shell listPlugins 2>/dev/null | grep -Fq "$plugin_id"; then
    break
  fi
  sleep 0.1
done
omarchy plugin enable "$plugin_id"
hyprctl reload
errors=$(hyprctl configerrors)
if [[ -n $errors && $errors != "no flags were set" && $errors != "-- No entries --" ]]; then
  printf '%s\n' "$errors" >&2
  exit 1
fi

rm -f -- "$old_bin_path" "$old_emit_path" "$old_ingest_path"

printf 'Keyboard Coach installed and enabled.\n'
printf 'Restart Chromium-based browsers for the accessibility flag, and log out once so Qt\n'
printf 'and KDE applications publish their menus. Run `keyboard-coach doctor` to check.\n'
