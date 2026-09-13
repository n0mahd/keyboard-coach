# Keyboard Coach

Keyboard Coach observes left, right, and middle clicks through passive Hyprland bindings. A
small systemd user daemon keeps AT-SPI and the compiled command catalog warm; the bindings send it
tiny Unix-socket events and the original click still reaches the focused application. The daemon
captures the semantic control on button press, before the UI can move or replace that control, and
shows at most one suggestion per six-second cooldown.

The catalog is generated from `assets/base-catalog.json` and sourced application command packs in
`assets/commands/`. `scripts/ingest_catalog.py` validates every pack, compiles high-priority semantic
matches, and retains the complete app command set as constrained context for Luna. A deterministic
match appears immediately without contacting OpenAI.

Accessibility labels are normalized into semantic intents before catalog matching. Browser labels
such as “More options”, “More actions”, “Application menu”, and “Customize and control …” all map
to `open-application-menu`; browser command packs bind that stable intent to the installed
accelerator. UI wording changes therefore do not require duplicate shortcut rules.

For Brave, ingestion reads only the `brave.accelerators` object from the active profile's
`Preferences` file. Command IDs are mapped against Brave and Chromium's published registries, so
the generated pack follows the shortcuts actually assigned on this machine, including customized
bindings. No browsing history, URLs, form data, cookies, or other profile values are read.
Brave's New Tab action also has a local post-click fallback because the browser moves the `+`
control before the release handler can inspect it.

For Omarchy itself, a passive button-press snapshot records the active layer before a menu can close.
The coach combines Omarchy's live bar geometry with installed first- and third-party plugin
manifests. It recognizes panel widgets from the same `open`/`close`/`opened` contract Omarchy uses,
so newly installed panel plugins work without adding their names to the coach. Menus and keyboard
panels use their native arrow/HJKL, Enter, Space, Tab, and Escape navigation. Standard accessible
buttons, links, toggles, and list items in other apps also have generic deterministic mappings.

Omarchy panel shortcuts are calculated from their current visible order. They use `Super+Ctrl+1`
through `Super+Ctrl+9`, matching Omarchy's own numbered panel bindings and adapting when the bar is
rearranged.

For launcher actions that are not covered by an app command pack, the coach observes the post-click
window or layer and joins it to installed desktop entries and Hyprland's effective live bindings.
This discovers shortcuts added by Omarchy, application packages, plugins, or the user after the
coach was installed. It only presents an exact active binding; an app with no binding still falls
through to the normal accessibility guidance or Luna.

The runtime index also inventories Omarchy's installed applications, effective Hyprland and global
shortcuts, command registry, base packages, and first- and third-party shell plugins. It checks for
changes every ten seconds and rebuilds the catalog atomically, so installing an app, changing a
binding, updating Omarchy, or downloading a plugin does not require reinstalling the coach. Plugins
can publish verified internal shortcuts with the [plugin shortcut contract](docs/shortcut-contract.md).
Conservative QML discovery is used for coverage reporting only until a shortcut has a proven action.

Only unmatched actions invoke `gpt-5.6-luna`. Luna receives structured metadata plus the focused
app's ingested commands, so it chooses from known shortcuts instead of inventing one. The coach does
not capture a screenshot or use OCR. Suggestions appear in a compact keyboard-marked banner for two
seconds.

Run `scripts/install.sh` to install the executable, shell panel, and passive Hyprland bindings.
The installer migrates an existing Mouse Keyboard Coach installation to the renamed service,
commands, data paths, Hyprland bindings, and Omarchy panel. Legacy data directories are retained as
a rollback copy but are no longer read by the running coach.
Set `KEYBOARD_COACH_COOLDOWN` in the desktop environment to change the default six-second
cooldown.

Press `Super + Ctrl + Alt + M` to pause or resume coaching. The commands
`keyboard-coach pause`, `resume`, `toggle`, and `status` provide the same control from a terminal.
Run `keyboard-coach coverage` for inventory and unmatched-action diagnostics.
Run `keyboard-coach inspect` while pointing at a control to see its semantic metadata. Extend
`~/.local/share/keyboard-coach/catalog.json` with app-specific mappings as needed. Chromium-based
browsers must be restarted once after installation so their accessibility flag takes effect.

To add or override an app, place a version-1 command pack in
`~/.config/keyboard-coach/commands/`, run `keyboard-coach-ingest`, and restart the daemon
with `systemctl --user restart keyboard-coach`.
