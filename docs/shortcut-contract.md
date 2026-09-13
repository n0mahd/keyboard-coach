# Plugin shortcut contract

Omarchy plugins can make their internal shortcuts deterministic by shipping a `shortcuts.json`
file beside `manifest.json`, or by pointing to another file:

```json
{
  "keyboardCoach": {
    "shortcuts": "docs/shortcuts.json"
  }
}
```

The shortcut file uses this shape:

```json
{
  "schema_version": 1,
  "shortcuts": [
    {
      "intent": "refresh",
      "shortcut": "Ctrl+R",
      "description": "refresh the panel.",
      "when": "panel is open",
      "match": {
        "names": ["refresh|reload"],
        "roles": ["button|menu item"]
      }
    }
  ]
}
```

`shortcut` and `description` are required. `intent`, `when`, and `match` are optional. Match values
use the same case-insensitive regular expressions as application command packs. The coach scopes a
plugin rule to the plugin's layer namespace unless `namespaces` is explicitly supplied.

Declared shortcuts are eligible for immediate deterministic suggestions. Shortcuts found by static
QML inspection appear in coverage diagnostics, but are not suggested until a semantic action can be
proven. This distinction prevents source-code guesses from being presented as active shortcuts.
