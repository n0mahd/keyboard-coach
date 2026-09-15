-- Fixture shaped like Omarchy's config: a helper that wraps commands in
-- dispatcher closures, generated bindings, unbinds, and side effects that the
-- replay must refuse.

o = {}

function o.bind(keys, description, dispatcher, options)
  local opts = options or {}
  opts.description = description
  if type(dispatcher) == "string" then
    dispatcher = hl.dsp.exec_cmd(dispatcher)
  end
  hl.bind(keys, dispatcher, opts)
end

hl.config({ general = { layout = "scrolling" } })
hl.on("hyprland.start", function() error("callbacks must not run") end)

o.bind("SUPER + SPACE", "Omarchy menu", "omarchy-menu toggle")
o.bind("SUPER + CTRL + A", "Audio", "omarchy-shell shell toggle omarchy.audio")
o.bind("SUPER + W", "Close window", hl.dsp.window.close())
o.bind("SUPER + CTRL + Z", "Zoom in", function() end)
o.bind("SUPER + SHIFT + M", "Music", "spotify")

for panel = 1, 3 do
  o.bind("SUPER + CTRL + code:" .. tostring(panel + 9), "Bar panel " .. panel,
    "omarchy-shell -q shell togglePanelAt right " .. panel)
end

hl.bind("mouse:272", hl.dsp.exec_cmd("keyboard-coach-emit click left"), { click = true, non_consuming = true })

hl.unbind("SUPER + SHIFT + M")

if hl.get_active_window() then
  o.bind("SUPER + X", "Never registered", "false")
end

os.execute("touch /tmp/keyboard-coach-replay-must-not-run")
local handle = io.open((os.getenv("KEYBOARD_COACH_REPLAY_PROBE") or "/tmp/keyboard-coach-probe"), "w")
if handle then
  handle:write("replay wrote a file")
  handle:close()
end
print("config output must not reach stdout")
