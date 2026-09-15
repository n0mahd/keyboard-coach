# Keyboard Coach

[![Built for Omarchy](https://raw.githubusercontent.com/tcballard/omarchy-badges/85f859029e236e784e7b05ada6dbe73506d07a91/badges/v1/built-for-omarchy.svg)](https://github.com/tcballard/omarchy-badges)

Keyboard Coach watches your mouse clicks on Omarchy and shows the keyboard shortcut that would have
done the same thing. Suggestions are deterministic: each one comes from an indexed source, and a
click with no reliable keyboard equivalent shows nothing.

## How a click is resolved

Passive Hyprland bindings send tiny press and release events to a systemd user daemon over a Unix
socket; the click still reaches the application. On press, the daemon captures what is under the
pointer before the UI can change:

- **Omarchy surfaces.** The layer under the pointer comes from Hyprland's IPC socket. For the bar,
  Omarchy's live bar geometry identifies the widget and its panel position.
- **Windows.** The window under the pointer comes from Hyprland, and the control under the pointer
  from AT-SPI. Wayland gives accessible windows no screen position, so the point is translated
  into window-local coordinates, and scaled inside Chromium/Electron web documents, which publish
  physical pixels. Controls inside a web page are marked as page content and carry the page host.

- **herdr.** herdr draws its sidebar, tabs and panes as terminal text, which has no accessible
  controls. When the window runs a herdr client, the daemon follows that session's API events and
  resolves the click by what it changed: focusing tab 3 is `Alt+3`, the next workspace in the
  sidebar is `Alt+Down`, the pane to the left is `Ctrl+Alt+Left`. Keys come from the herdr
  shortcut index, so they follow your `config.toml`.

The first matching source wins:

1. App command packs from `assets/commands/` and `~/.config/keyboard-coach/commands/`, plus
   shortcuts Omarchy plugins declare through the [plugin shortcut contract](docs/shortcut-contract.md).
2. Omarchy bindings. Hyprland's Lua config reports every binding command as an opaque `__lua`
   reference, so ingestion replays the config in a sandboxed Lua interpreter
   (`scripts/hypr-bind-dump.lua`) to recover each binding's keys, description, and command. A bar
   widget resolves to its named binding (`Super+Ctrl+A` for Audio), then to its `Bar panel N`
   binding.
3. Shortcuts the control publishes itself: `aria-keyshortcuts` on web pages, accelerators on GTK
   and Qt menu items, or a shortcut at the end of its label or tooltip, such as `Play (k)`.
4. Application shortcuts from the shortcut index (below), matched by the control's label.
5. The Omarchy app launcher, for bar widgets named after an installed app.
6. The base catalog in `assets/base-catalog.json`: Omarchy menu navigation and a few anchored,
   non-web edit commands.

Browser packs match only browser UI (`default_match` scopes them away from page content), so a link
named "Previous" on a website is not reported as `Alt+Left`.

For Brave, ingestion reads only the `brave.accelerators` object from the default profile's
`Preferences` and maps Brave and Chromium command IDs onto it, so suggestions follow customized
bindings.

## Shortcut index

`keyboard-coach-harvest`, written in Rust, reads each installed application's own shortcut
definitions without running it, into `~/.local/share/keyboard-coach/shortcuts.json`:

- GTK 3, GTK 4 and libadwaita apps: shortcut windows, menus and accelerators compiled into the
  executable's GResource bundles.
- LibreOffice: its accelerator and command-label registry, per module.
- foot, mpv and imv: their shipped defaults with the user's config applied on top.
- lazygit's default config, and Omarchy's tmux and herdr keybinding menus.

Qt, KDE, Electron and web apps have no such file. For those, the daemon captures shortcuts from the
running application's accessibility tree in the background after a click, at most once every 15
minutes per window (and per site for web pages), into `observed.json`:

- Qt and GTK report the accelerator of every menu item, including items of closed menus. Mnemonics
  (`Alt+N` for `Read-O&nly`) are ignored inside menus, where they only work while the menu is open.
- Web pages report `aria-keyshortcuts` and tooltips such as `Play (k)`. Page shortcuts are kept per
  site and only match controls on that site. A binding shared by several differently named
  controls moves within a region rather than activating one of them, so it is dropped.

Captures accumulate: a shortcut stays known after its menu or tooltip is gone, and a newer capture
of the same title replaces an older one. Application shortcuts never match controls inside web pages.

The daemon re-indexes after changes to Hyprland config, Omarchy plugins, desktop entries, command
packs, Brave preferences, or the shortcut sources above. A failed re-index is logged and retried on
the next change.

## Install

Run `scripts/install.sh`. It requires `socat`, a Lua 5.5 interpreter (`lua`), and a Rust toolchain
(`cargo`, or `mise` with this repository's `mise.toml`) to build the harvester. It installs the
executables, the Omarchy banner panel, and the passive Hyprland bindings, and enables the service
under `graphical-session.target`. Chromium-based browsers must be restarted once after installation
so their accessibility flag takes effect.

## Use

- `Super + Ctrl + Alt + M`, or `keyboard-coach toggle`, pauses and resumes coaching. `pause`,
  `resume`, and `status` do the same from a terminal.
- `keyboard-coach last` prints the context and suggestion for the most recent click.
- `keyboard-coach inspect 3` waits three seconds, then prints the context under the pointer.
- `keyboard-coach coverage` reports indexed sources, shortcuts per app, and the apps with unmatched
  clicks.
- `keyboard-coach-harvest --summary` lists what each app's static sources provide, and
  `keyboard-coach-harvest observe --pid PID --class CLASS --print` captures a running window now.
- Every click is resolved: a new suggestion replaces the banner on screen, and a click with no
  suggestion clears a banner left from an earlier click.

## Develop

`tests/corpus/cases.json` holds click contexts and the suggestion each must produce, including clicks
that must stay silent. It runs against `tests/corpus/catalog.json`, which is compiled from the shipped
assets and fixtures; run `tests/corpus/build_catalog.py` after changing either. Every implementation
reads the same corpus.

```sh
python3 -m unittest discover -s tests
mise exec -- cargo test
```
