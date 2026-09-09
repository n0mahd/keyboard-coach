---
name: keyboard-coach
description: Explain a mouse action and suggest an equivalent keyboard shortcut using GPT Luna.
---

# Keyboard Coach

Use this skill when the user asks how to replace a mouse action with a keyboard binding or shortcut.

The local watcher identifies the focused app and the semantic control under the pointer through AT-SPI. It first uses a key binding published by the control, then checks the local app/role/name/action catalog. When either matches, it shows that shortcut locally. Only unmatched clicks invoke Codex with `gpt-5.6-luna`, sending structured app and accessibility metadata without a screenshot. Luna should return one concise suggestion in this form:

`Shortcut — what it does (and how to enable it, if it is not already available)`

Keep suggestions specific to the focused application when that context is available. Do not invent a shortcut when the application has no reliable equivalent; say that a custom binding is needed.

The watcher displays Luna's response through the top-center Omarchy panel. Typed text, screenshots, and raw mouse coordinates must not be sent.

Omarchy bar widgets are identified through the shell's live geometry IPC. Omarchy menus and keyboard panels should use their native arrow or H/J/K/L navigation, Enter or Space to activate, Tab to change sections, and Escape to close. The banner has no input region and requires no keyboard equivalent.

For visible Omarchy bar widgets that own panels, derive the shortcut from the widget's current one-based panel position: `Super+Ctrl+1` through `Super+Ctrl+9`. Do not substitute an application-launch shortcut for a panel shortcut; for example, Agents usage is a numbered panel while the agent picker is a separate action.
