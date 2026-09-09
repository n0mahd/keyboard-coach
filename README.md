# Keyboard Coach

Keyboard Coach observes completed left, right, and middle clicks through passive Hyprland
bindings. The original click still reaches the focused application. After a six-second cooldown,
it identifies the focused app and asks AT-SPI for the accessible control and action under the pointer.
It first uses a shortcut published by that control, then checks the semantic action catalog in
`assets/catalog.json`. A deterministic match appears immediately without contacting OpenAI.

For Omarchy itself, a passive button-press snapshot records the active layer before a menu can close.
The coach uses Omarchy's live bar geometry to identify Menu, Workspaces, Clock, Agents, Bluetooth,
Network, Audio, Display, and Power. Menus and keyboard panels use their native arrow/HJKL, Enter,
Space, Tab, and Escape navigation. Standard accessible buttons, links, toggles, and list items in
other apps also have generic deterministic mappings.

Omarchy panel shortcuts are calculated from their current visible order. They use `Super+Ctrl+1`
through `Super+Ctrl+9`, matching Omarchy's own numbered panel bindings and adapting when the bar is
rearranged.

Only unmatched actions invoke `gpt-5.6-luna`. Luna receives structured metadata such as the app,
window title, control role, control name, and available actions. The coach does not capture a
screenshot or use OCR. Suggestions appear in a compact keyboard-marked banner for two seconds.

Run `bash scripts/install.sh` to install the executable, shell panel, and passive Hyprland bindings.
Set `KEYBOARD_COACH_COOLDOWN` in the desktop environment to change the default six-second
cooldown.

Press `Super + Ctrl + Alt + M` to pause or resume coaching. The commands
`keyboard-coach pause`, `resume`, `toggle`, and `status` provide the same control from a terminal.
Run `keyboard-coach inspect` while pointing at a control to see its semantic metadata. Extend
`~/.local/share/keyboard-coach/catalog.json` with app-specific mappings as needed. Chromium-based
browsers must be restarted once after installation so their accessibility flag takes effect.
