---
name: keyboard-coach
description: Explain a mouse action and suggest an equivalent keyboard shortcut using GPT Luna.
---

# Keyboard Coach

Use this skill when the user asks how to replace a mouse action with a keyboard binding or shortcut.

The local daemon identifies the focused app and captures the semantic control under the pointer on button press through AT-SPI. It normalizes that metadata into semantic intents and checks authoritative app command packs, effective Hyprland bindings, declared plugin shortcuts, application-published accelerators, then generic role/action rules. This ordering prevents incidental bindings such as Escape on a menu from overriding the action the user actually chose. Only unmatched clicks invoke Codex with `gpt-5.6-luna`, sending structured app and accessibility metadata plus the app's ingested command set without a screenshot. Luna should prefer an ingested command and return one concise suggestion in this form:

For Brave, the ingestion step resolves published Brave/Chromium command IDs against the effective `brave.accelerators` profile preference. Recommendations therefore use the user's installed bindings rather than assuming the documented defaults. The ingestor reads only that accelerator object from the profile.

`Shortcut — what it does (and how to enable it, if it is not already available)`

Keep suggestions specific to the focused application when that context is available. Do not invent a shortcut when the application has no reliable equivalent; say that a custom binding is needed.

The catalog is a live installation index, not a fixed list. It inventories desktop launchers, the Omarchy command registry and base packages, effective Hyprland/global shortcuts, and packaged or downloaded shell plugins. The daemon rebuilds it after source changes. `keyboard-coach coverage` reports provider coverage and privacy-minimized unmatched actions. QML source discoveries are diagnostic only unless a semantic action is proven; plugin-declared shortcut contracts and effective bindings are deterministic sources.

The watcher displays Luna's response through the top-center Omarchy panel. Typed text, screenshots, and raw mouse coordinates must not be sent.

Omarchy bar widgets are identified through the shell's live geometry IPC. Omarchy menus and keyboard panels should use their native arrow or H/J/K/L navigation, Enter or Space to activate, Tab to change sections, and Escape to close. The banner has no input region and requires no keyboard equivalent.

For visible Omarchy bar widgets that own panels, derive the shortcut from the widget's current one-based panel position: `Super+Ctrl+1` through `Super+Ctrl+9`. Do not substitute an application-launch shortcut for a panel shortcut; for example, Agents usage is a numbered panel while the agent picker is a separate action.
