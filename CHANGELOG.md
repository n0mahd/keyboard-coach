# Changelog

## 0.2.0 — 2026-09-21

Keyboard Coach was built against one setup: one browser, one set of installed applications, and one
person's tolerance for being interrupted. This release widens each of those, and says plainly what
the coach keeps about you.

### The browser menu button produces a suggestion

Clicking the hamburger menu in Brave suggested nothing at all. Its accessible name is `Brave` and
its description is `Customize and control Brave`, while Chrome puts the whole phrase in the name;
the coach tested the two fields joined together, so neither form matched anything. Each field is now
tested on its own, and both resolve to the same intent, so the button works wherever it is labelled
differently — which is every browser.

### Browsers are covered as families

One pack now serves Chrome, Chromium, Brave, Vivaldi, Edge and Thorium, and another serves Firefox,
Zen, LibreWolf and Floorp. Only what a single browser has, such as Brave's Tor window, stays in that
browser's own pack.

This also covers the separate windows a browser opens for an installed web app, whose window class
looks like `brave-x.com__-Default`. Those matched no pack before, so every click in a web app was
silent.

Shortcuts you reassigned inside a browser are read from that browser's own preferences and applied
only to its windows, so one browser's custom keys are never suggested inside another.

### KDE and Visual Studio Code applications are indexed without being run

KDE applications inherit a standard set of shortcuts, and with the reassignments from the
application's own configuration and `kdeglobals` layered on top, a KDE application is covered from
the moment it is installed. On the machine this was developed on that took kdenlive from nothing to
33 shortcuts without ever opening it.

Visual Studio Code, VSCodium, Code - OSS, Cursor and Windsurf get the defaults for the commands
behind their clickable views, with your own `keybindings.json` layered on top.

### Applications are learned when they open, not when they are clicked

The coach captures what a window publishes about itself through the accessibility tree. It used to
do that only after you had clicked in the window, so the first click in any application was always
wasted. It now captures a window class once, as its first window opens, and keeps the existing
capture after a click at most every fifteen minutes.

### `keyboard-coach doctor`

Coverage depends almost entirely on whether an application's toolkit publishes anything at all, and
there was no way to find out. `doctor` reports, for every open window, whether the coach can see it,
what its toolkit is, how many shortcuts are indexed for it, and the single setting that would change
the answer. It also checks the click bindings, the catalog, and each toolkit's accessibility
setting.

The installer now turns on the last of those settings itself, writing
`QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1` to `~/.config/environment.d/90-keyboard-coach.conf` so Qt and
KDE applications publish their menus. That file is read when a session starts, so it takes effect
after one logout; the uninstaller removes it.

### Coaching on your terms

`~/.config/keyboard-coach/config.json` is new, and every key in it is optional. A suggestion can be
held back until the same action has been clicked a few times, so a one-off click stays quiet and a
habit does not. A suggestion you have already seen often enough can stop repeating. Applications and
individual shortcuts can be muted with a regular expression — muting a shortcut is how you retire
one you have learned — and quiet hours stop the banner overnight. A malformed file falls back to the
shipped behaviour rather than stopping the coach.

None of this changes which shortcut a click resolves to. It only decides whether the banner appears.

`keyboard-coach report` ranks the clicks you make that already have a keyboard equivalent and says
whether you are making fewer of them than the period before.

### Privacy

Resolving a click means looking at whatever is under the pointer, so the coach can see window
titles, control labels and the pages you are reading. What it keeps is now stated, enforced and
reversible. The README has a Privacy section; in short:

- Nothing leaves the machine. Neither program contains network code or depends on a network library,
  and a test fails if that ever changes.
- `ignore_apps` in the settings file puts a window class out of reach entirely: no accessibility
  read, not even its title, no background capture, nothing written down. It is the right setting for
  a password manager. Muting is not the same thing — a muted application is still read, it just
  says nothing.
- `"history": false` stops both local logs being written.
- `keyboard-coach doctor` ends with every file the coach holds, its size and its mode.
- `keyboard-coach forget` deletes all of them, captures included, and leaves your settings and
  command packs alone.

Three gaps behind those promises were closed. The log of clicks with no known shortcut was created
readable by anyone on the machine and grew without limit; it is now private and bounded. Files
written by an earlier version kept the permissions they were created with, because the mode was only
applied at creation; it is now set on every write. The index directory is created private. Tests
assert the file modes, assert that the log keeps a control's role and a page's host but never a
label, title or filename, and fail if either program gains a way to reach the network.

## 0.1.0 — 2026-09-16

First release, prepared for the Omarchy plugin marketplace: click resolution through Hyprland's IPC
and the AT-SPI accessibility tree, the Rust shortcut index built from installed applications' own
definitions, Omarchy bar and herdr support, and the banner panel.
