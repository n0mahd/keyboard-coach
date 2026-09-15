-- Evaluate the user's Hyprland Lua config against a recording stub and print
-- every effective binding as JSON.
--
-- Hyprland 0.56 reports Lua bindings as `__lua <ref>` in `hyprctl binds`, and
-- Omarchy's o.bind wraps commands in closures before registering them, so the
-- command text never reaches the compositor. Replaying the config in a separate
-- interpreter recovers the key string, description, and command for every bind,
-- including ones generated in loops or guarded by conditionals.
--
-- Nothing here talks to the running compositor. Callbacks (hl.on, hl.timer,
-- bound functions) are never invoked, and writes to the filesystem are refused.
--
-- usage: lua hypr-bind-dump.lua [path/to/hyprland.lua]

local real_stdout = io.stdout
local config_home = os.getenv("XDG_CONFIG_HOME")
if config_home == nil or config_home == "" then
  config_home = (os.getenv("HOME") or "") .. "/.config"
end
local entry = arg[1] or (config_home .. "/hypr/hyprland.lua")

local bindings = {}
local errors = {}

-- Sandbox: config files may probe the system with io.popen and io.open(..., "r"),
-- but must not change anything while being replayed.
os.execute = function() return false end
os.remove = function() return nil, "keyboard-coach: refused" end
os.rename = function() return nil, "keyboard-coach: refused" end
os.exit = function() error("keyboard-coach: os.exit refused") end
local real_open = io.open
io.open = function(path, mode)
  if mode and mode:find("[wa+]") then
    return nil, "keyboard-coach: write refused"
  end
  return real_open(path, mode)
end
print = function() end
io.write = function() return io.stdout end

local function caller_source()
  -- Report the line that called the bind helper, so a bind made through
  -- o.bind/o.bind_toggle points at bindings/*.lua rather than at the helper.
  -- Level 1 is this function and level 2 is the hl.bind stub.
  local fallback
  local level = 3
  while level <= 16 do
    local info = debug.getinfo(level, "Sln")
    if not info then break end
    if info.currentline and info.currentline > 0 then
      local where = info.short_src .. ":" .. info.currentline
      fallback = fallback or where
      if not (info.name and info.name:match("^bind")) then
        return where
      end
    end
    level = level + 1
  end
  return fallback or ""
end

local function normalize_keys(keys)
  return (tostring(keys):upper():gsub("%s+", ""))
end

-- Any hl.<path>(...) that is not specifically recorded returns a marker naming
-- the dispatcher, so `hl.dsp.window.resize({...})` becomes {kind="dispatcher"}.
local function proxy(path)
  return setmetatable({}, {
    __index = function(_, key)
      return proxy(path == "" and key or (path .. "." .. key))
    end,
    __call = function(_, ...)
      if path:match("^dsp%.") then
        return { kind = "dispatcher", name = path:sub(5) }
      end
      if path:match("^get_") then
        return nil
      end
      return proxy(path .. "()")
    end,
  })
end

local hl_stub = proxy("")
local hl_overrides = {}

hl_overrides.bind = function(keys, dispatcher, options)
  local record = {
    keys = tostring(keys),
    source = caller_source(),
  }
  if type(dispatcher) == "table" and dispatcher.kind then
    record.kind = dispatcher.kind
    record.command = dispatcher.command
    record.dispatcher = dispatcher.name
  elseif type(dispatcher) == "function" then
    record.kind = "lua-function"
  else
    record.kind = type(dispatcher)
  end
  local flags = {}
  if type(options) == "table" then
    for key, value in pairs(options) do
      if key == "description" then
        record.description = tostring(value)
      elseif type(value) == "boolean" or type(value) == "number" or type(value) == "string" then
        flags[key] = value
      end
    end
  end
  record.flags = flags
  -- A later bind of the same keys in the same submap does not replace the first
  -- in Hyprland; both fire. Keep every record and let unbind remove them.
  bindings[#bindings + 1] = record
end

hl_overrides.unbind = function(keys)
  local wanted = normalize_keys(keys)
  local kept = {}
  for _, record in ipairs(bindings) do
    if normalize_keys(record.keys) ~= wanted then
      kept[#kept + 1] = record
    end
  end
  bindings = kept
end

local dsp = proxy("dsp")
local dsp_overrides = {
  exec_cmd = function(command)
    return { kind = "exec", command = tostring(command) }
  end,
}

hl = setmetatable({}, {
  __index = function(_, key)
    if hl_overrides[key] then return hl_overrides[key] end
    if key == "dsp" then
      return setmetatable({}, {
        __index = function(_, name)
          return dsp_overrides[name] or dsp[name]
        end,
      })
    end
    return hl_stub[key]
  end,
})

local ok, failure = pcall(dofile, entry)
if not ok then
  errors[#errors + 1] = tostring(failure)
end

-- Minimal JSON encoder; the output is consumed by keyboard-coach-ingest.
local function encode(value)
  local kind = type(value)
  if kind == "nil" then
    return "null"
  elseif kind == "boolean" or kind == "number" then
    return tostring(value)
  elseif kind == "string" then
    return '"' .. value:gsub('[%c"\\]', function(char)
      local named = { ['"'] = '\\"', ["\\"] = "\\\\", ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t" }
      return named[char] or string.format("\\u%04x", char:byte())
    end) .. '"'
  elseif kind == "table" then
    if #value > 0 or next(value) == nil and getmetatable(value) == "array" then
      local parts = {}
      for index = 1, #value do
        parts[index] = encode(value[index])
      end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local keys = {}
    for key in pairs(value) do
      keys[#keys + 1] = tostring(key)
    end
    table.sort(keys)
    local parts = {}
    for _, key in ipairs(keys) do
      parts[#parts + 1] = encode(key) .. ":" .. encode(value[key])
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return "null"
end

local function array(items)
  return setmetatable(items, { __metatable = "array" })
end

real_stdout:write(encode({
  entry = entry,
  bindings = array(bindings),
  errors = array(errors),
}), "\n")
