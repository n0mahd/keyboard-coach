-- Keyboard Coach: passive click observers. The click still reaches the app.
o.bind("mouse:272", nil, "keyboard-coach snapshot left", { non_consuming = true })
o.bind("mouse:273", nil, "keyboard-coach snapshot right", { non_consuming = true })
o.bind("mouse:274", nil, "keyboard-coach snapshot middle", { non_consuming = true })
o.bind("mouse:272", nil, "keyboard-coach click left", { click = true, non_consuming = true })
o.bind("mouse:273", nil, "keyboard-coach click right", { click = true, non_consuming = true })
o.bind("mouse:274", nil, "keyboard-coach click middle", { click = true, non_consuming = true })
o.bind("SUPER + CTRL + ALT + M", "Toggle Keyboard Coach", "keyboard-coach toggle")
