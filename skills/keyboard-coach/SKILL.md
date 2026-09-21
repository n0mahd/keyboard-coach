---
name: keyboard-coach
description: Explain a mouse action and suggest an equivalent keyboard shortcut from Keyboard Coach's deterministic shortcut index.
---

# Keyboard Coach

Use this skill when the user asks how to replace a mouse action with a keyboard binding or shortcut, or wants to extend Keyboard Coach's coverage.

Keyboard Coach is deterministic: every suggestion comes from an indexed source, and a click with no reliable equivalent produces no suggestion. Do not invent shortcuts.

The daemon resolves a click in this order:

1. The window or Omarchy layer under the pointer, from Hyprland's IPC socket.
2. For windows, the accessible control under the pointer through AT-SPI. Wayland gives accessible windows no screen position, so the point is translated into window-local coordinates, and scaled inside Chromium/Electron web documents. Controls inside a web page are marked `in_document` with the page's host in `site`.
3. For a terminal running a herdr client, the herdr state before and after the click, from the session's API events: a change of focused tab, workspace or pane, or of zoom, maps to the herdr command with the same effect.
4. Suggestion sources, first match wins: app command packs (and plugin shortcut contracts), Omarchy bindings indexed from the Hyprland Lua config, shortcuts the control publishes itself (`aria-keyshortcuts`, GTK and Qt accelerators, a trailing `(Ctrl+B)` in its label or tooltip), the shortcut index matched by label, the app launcher for bar widgets named after installed apps, then the base catalog.

The shortcut index has two parts. `keyboard-coach-harvest` reads installed apps' static definitions (GTK GResources, the LibreOffice registry, the KDE `KStandardShortcut` set with the user's `[Shortcuts]` overrides, VS Code-family `keybindings.json`, foot/mpv/imv/lazygit configs, Omarchy's tmux and herdr menus) into `shortcuts.json`. For Qt, Electron and web apps, the daemon runs `keyboard-coach-harvest observe` in the background — once per window class when its first window opens, and again after a click at most every 15 minutes — to capture menu accelerators, `aria-keyshortcuts` and tooltip shortcuts from the live accessibility tree into `observed.json`; page shortcuts are scoped to their site.

Whether an app can be seen at all depends on its toolkit's accessibility setting: GTK needs `toolkit-accessibility`, Qt and KDE need `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`, and Chromium and Electron need `--force-renderer-accessibility`. Check that first when an app produces nothing; `keyboard-coach doctor` reports it per window.

Hyprland's Lua config hides binding commands from `hyprctl binds`, so ingestion replays the config in a sandboxed Lua interpreter to recover each binding's keys, description, and command.

To extend coverage:

- Run `keyboard-coach doctor` to see, per open window, whether it is visible to the coach, its toolkit, its indexed shortcut count, and the setting that would fix it.
- Run `keyboard-coach coverage` to see shortcuts per app and which apps produce unmatched clicks.
- Run `keyboard-coach-harvest observe --pid PID --class CLASS --print` to see what a running window publishes.
- Run `keyboard-coach last` after a click, or `keyboard-coach inspect 3` and point at a control, to see the captured context.
- Add a command pack under `~/.config/keyboard-coach/commands/`. Browser packs should set `"default_match": {"target_in_document": ["^false$"]}` so they only match browser UI, never page content.
- Add a case to `tests/corpus/cases.json` for every behavior change, including cases that must stay silent.

Browser coverage is by family: `chromium.json` and `chromium-native.json` cover Chrome, Chromium, Brave, Vivaldi, Edge, Thorium and the app windows they install for web apps; `firefox.json` covers Firefox, Zen, LibreWolf and Floorp; `brave.json` holds only the commands Brave alone has. Ingestion resolves Chromium command IDs against each installed browser's `Default` profile `Preferences`, and scopes a reassignment to that browser's own windows.

Controls whose label differs per browser are matched by intent instead: `semantic_intents` in `scripts/coach.py` turns a hamburger button's name or description into `open-menu` and `open-application-menu`, and packs match on `intents`. Name and description are tested separately, because which of the two carries the label differs per application.

Coaching policy is separate from resolution: `~/.config/keyboard-coach/config.json` can hold back a suggestion until an action repeats, mute apps or suggestions, and set quiet hours. It never changes which shortcut a click resolves to, only whether the banner appears.

For Omarchy bar widgets that own panels, prefer the widget's named binding (for example `Super+Ctrl+A` for Audio), then the numbered `Bar panel N` binding for its position in the right section.
