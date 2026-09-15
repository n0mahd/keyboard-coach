//! Canonical rendering of key combinations from every source format.
//!
//! Every harvester reduces its native syntax (`<Primary><shift>n`, `S_SHIFT_MOD1`,
//! `Control+Shift+c`, `<ctrl+c>`) to the same display form, `Ctrl+Shift+N`, so
//! shortcuts from different sources compare and render identically.

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Modifier {
    Super,
    Ctrl,
    Alt,
    Shift,
}

impl Modifier {
    fn label(self) -> &'static str {
        match self {
            Modifier::Super => "Super",
            Modifier::Ctrl => "Ctrl",
            Modifier::Alt => "Alt",
            Modifier::Shift => "Shift",
        }
    }
}

pub fn modifier(name: &str) -> Option<Modifier> {
    match name.to_ascii_lowercase().as_str() {
        "super" | "win" | "logo" | "meta" | "mod4" | "hyper" => Some(Modifier::Super),
        // `mod`, `CmdOrCtrl`: the platform command key, Ctrl on Linux.
        "primary" | "control" | "ctrl" | "ctl" | "mod" | "cmdorctrl" | "commandorcontrol" => Some(Modifier::Ctrl),
        "alt" | "mod1" | "option" => Some(Modifier::Alt),
        "shift" => Some(Modifier::Shift),
        _ => None,
    }
}

/// Whether letter case distinguishes keys. GUI accelerators ignore case (`n`
/// and `N` are the same key); terminal programs treat `q` and `Q` differently.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Case {
    Insensitive,
    Sensitive,
}

/// Render a key name, or `None` for pointer buttons and wheel events, which
/// have no keyboard equivalent to teach. Names that are not recognized are
/// title-cased, since configuration files use many spellings of real keys.
pub fn key_name(name: &str, case: Case) -> Option<String> {
    render_key(name, case, true)
}

/// Like [`key_name`], but only for names known to be keys. Used where text
/// might merely look like a key, such as the parenthesized part of a label.
pub fn known_key_name(name: &str, case: Case) -> Option<String> {
    render_key(name, case, false)
}

fn render_key(name: &str, case: Case, allow_unknown: bool) -> Option<String> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return None;
    }
    let lower = trimmed.to_ascii_lowercase();
    if lower.starts_with("mbtn") || lower.starts_with("wheel") || lower.starts_with("mouse") {
        return None;
    }
    let mut chars = trimmed.chars();
    if let (Some(only), None) = (chars.next(), chars.next()) {
        return Some(match case {
            Case::Insensitive => only.to_uppercase().collect(),
            Case::Sensitive => only.to_string(),
        });
    }
    let named = match lower.as_str() {
        "return" | "enter" | "kp_enter" | "ret" | "cr" => "Enter",
        "escape" | "esc" => "Esc",
        "space" | "spc" => "Space",
        "tab" => "Tab",
        "iso_left_tab" | "backtab" | "btab" => "Shift+Tab",
        "backspace" | "bspace" | "bs" => "Backspace",
        "delete" | "del" | "dc" | "kp_delete" => "Delete",
        "insert" | "ins" | "ic" | "kp_insert" => "Insert",
        "home" | "kp_home" => "Home",
        "end" | "kp_end" => "End",
        "page_up" | "pageup" | "pgup" | "prior" | "ppage" | "kp_page_up" | "kp_prior" => "PageUp",
        "page_down" | "pagedown" | "pgdown" | "pgdwn" | "next" | "npage" | "kp_page_down" | "kp_next" => "PageDown",
        "left" | "kp_left" | "arrowleft" => "Left",
        "right" | "kp_right" | "arrowright" => "Right",
        "up" | "kp_up" | "arrowup" => "Up",
        "down" | "kp_down" | "arrowdown" => "Down",
        "plus" | "add" => "+",
        "minus" | "subtract" => "-",
        "equal" => "=",
        "period" | "point" | "dot" => ".",
        "comma" => ",",
        "slash" | "divide" => "/",
        "backslash" => "\\",
        "semicolon" => ";",
        "colon" => ":",
        "apostrophe" | "quoteright" => "'",
        "grave" | "quoteleft" => "`",
        "asciitilde" | "tilde" => "~",
        "asciicircum" => "^",
        "bracketleft" => "[",
        "bracketright" => "]",
        "braceleft" => "{",
        "braceright" => "}",
        "parenleft" => "(",
        "parenright" => ")",
        "less" => "<",
        "greater" => ">",
        "question" => "?",
        "exclam" => "!",
        "at" => "@",
        "numbersign" | "sharp" => "#",
        "dollar" => "$",
        "percent" => "%",
        "ampersand" => "&",
        "asterisk" | "multiply" => "*",
        "underscore" => "_",
        "bar" => "|",
        "print" | "sysrq" => "Print",
        "menu" | "contextmenu" => "Menu",
        "kp_add" => "Num+",
        "kp_subtract" => "Num-",
        "kp_multiply" => "Num*",
        "kp_divide" => "Num/",
        "pause" => "Pause",
        _ => "",
    };
    if !named.is_empty() {
        return Some(named.to_string());
    }
    if let Some(digit) = lower.strip_prefix("kp_").filter(|rest| rest.len() == 1 && rest.chars().all(|c| c.is_ascii_digit())) {
        return Some(format!("Num{digit}"));
    }
    if lower.len() <= 3 && lower.starts_with('f') && lower[1..].chars().all(|c| c.is_ascii_digit()) {
        return Some(lower.to_ascii_uppercase());
    }
    if let Some(rest) = trimmed.strip_prefix("XF86") {
        return Some(rest.to_string());
    }
    if !allow_unknown {
        return None;
    }
    // Title-case anything else (`PAGEDOWN` was handled above; `Scroll_Lock`).
    let mut display = String::new();
    for (index, part) in lower.split('_').enumerate() {
        if index > 0 {
            display.push(' ');
        }
        let mut part_chars = part.chars();
        if let Some(first) = part_chars.next() {
            display.extend(first.to_uppercase());
            display.push_str(part_chars.as_str());
        }
    }
    Some(display)
}

/// Build the canonical form of one chord.
pub fn chord(modifiers: &[Modifier], key: &str, case: Case) -> Option<String> {
    let mut sorted = modifiers.to_vec();
    // Terminals deliver Ctrl+c and Ctrl+C identically, so case is only
    // significant for unmodified or Shift/Alt letters.
    let case = if sorted.contains(&Modifier::Ctrl) { Case::Insensitive } else { case };
    let key = key_name(key, case)?;
    sorted.sort();
    sorted.dedup();
    // Shift+Tab arrives from ISO_Left_Tab as a composite key name.
    if let Some(base) = key.strip_prefix("Shift+") {
        if !sorted.contains(&Modifier::Shift) {
            sorted.push(Modifier::Shift);
            sorted.sort();
        }
        return Some(join(&sorted, base));
    }
    Some(join(&sorted, &key))
}

fn join(modifiers: &[Modifier], key: &str) -> String {
    let mut parts: Vec<&str> = modifiers.iter().map(|m| m.label()).collect();
    parts.push(key);
    parts.join("+")
}

/// Parse GTK accelerator strings. A property can list several accelerators
/// separated by spaces: `Return <Primary>O`.
pub fn gtk_accelerators(text: &str) -> Vec<String> {
    text.split_whitespace().filter_map(gtk_accelerator).collect()
}

fn gtk_accelerator(text: &str) -> Option<String> {
    let mut modifiers = Vec::new();
    let mut rest = text;
    while let Some(stripped) = rest.strip_prefix('<') {
        let end = stripped.find('>')?;
        let name = &stripped[..end];
        // Unknown modifiers (Hyper, Release) make the accelerator unreliable.
        modifiers.push(modifier(name)?);
        rest = &stripped[end + 1..];
    }
    // Ranges such as `<alt>0...8` describe a family of shortcuts.
    if let Some((first, last)) = rest.split_once("...") {
        let first = chord(&modifiers, first, Case::Insensitive)?;
        let last = key_name(last, Case::Insensitive)?;
        return Some(format!("{first}…{last}"));
    }
    chord(&modifiers, rest, Case::Insensitive)
}

/// Parse `Modifier+Modifier+key` notation (foot, mpv, GTK action docs). A
/// trailing `+` is the plus key itself: `Alt++`.
pub fn plus_separated(text: &str, case: Case) -> Option<String> {
    let text = text.trim();
    if text.is_empty() {
        return None;
    }
    let (body, plus_key) = match text.strip_suffix("++") {
        Some(body) => (body, true),
        None if text == "+" => ("", true),
        None => (text, false),
    };
    let mut parts: Vec<&str> = if body.is_empty() { Vec::new() } else { body.split('+').collect() };
    let key = if plus_key { "+" } else { parts.pop()? };
    let mut modifiers = Vec::new();
    for part in parts {
        modifiers.push(modifier(part)?);
    }
    chord(&modifiers, key, case)
}

/// Parse LibreOffice accelerator node names such as `S_SHIFT_MOD1`.
pub fn libreoffice(name: &str) -> Option<String> {
    let mut parts = name.split('_');
    let key = parts.next()?;
    let mut modifiers = Vec::new();
    for part in parts {
        modifiers.push(match part {
            "SHIFT" => Modifier::Shift,
            "MOD1" => Modifier::Ctrl,
            "MOD2" => Modifier::Alt,
            // MOD3 is the macOS Control key; it has no binding on Linux.
            _ => return None,
        });
    }
    chord(&modifiers, key, Case::Insensitive)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gtk_accelerators_render_canonically() {
        assert_eq!(gtk_accelerators("<Primary><shift>N"), vec!["Ctrl+Shift+N"]);
        assert_eq!(gtk_accelerators("Return <Primary>O"), vec!["Enter", "Ctrl+O"]);
        assert_eq!(gtk_accelerators("<Primary>Page_Up"), vec!["Ctrl+PageUp"]);
        assert_eq!(gtk_accelerators("<Primary>plus"), vec!["Ctrl++"]);
        assert_eq!(gtk_accelerators("<alt>0...8"), vec!["Alt+0…8"]);
        assert_eq!(gtk_accelerators("asciitilde"), vec!["~"]);
        assert_eq!(gtk_accelerators("<Shift>ISO_Left_Tab"), vec!["Shift+Tab"]);
        assert!(gtk_accelerators("<Release>a").is_empty());
    }

    #[test]
    fn plus_separated_handles_plus_key_and_case() {
        assert_eq!(plus_separated("Control+Shift+c", Case::Insensitive).as_deref(), Some("Ctrl+Shift+C"));
        assert_eq!(plus_separated("Alt++", Case::Insensitive).as_deref(), Some("Alt++"));
        assert_eq!(plus_separated("Ctrl+-", Case::Insensitive).as_deref(), Some("Ctrl+-"));
        assert_eq!(plus_separated("Shift+RIGHT", Case::Insensitive).as_deref(), Some("Shift+Right"));
        assert_eq!(plus_separated("Q", Case::Sensitive).as_deref(), Some("Q"));
        assert_eq!(plus_separated("q", Case::Sensitive).as_deref(), Some("q"));
        assert_eq!(plus_separated("ctrl+c", Case::Sensitive).as_deref(), Some("Ctrl+C"));
        assert_eq!(plus_separated("PGDWN", Case::Sensitive).as_deref(), Some("PageDown"));
        assert_eq!(plus_separated("Alt+ArrowUp", Case::Insensitive).as_deref(), Some("Alt+Up"));
        assert_eq!(plus_separated("MBTN_LEFT", Case::Insensitive), None);
        assert_eq!(plus_separated("Hyperx+a", Case::Insensitive), None);
    }

    #[test]
    fn libreoffice_key_names() {
        assert_eq!(libreoffice("S_MOD1").as_deref(), Some("Ctrl+S"));
        assert_eq!(libreoffice("N_SHIFT_MOD1").as_deref(), Some("Ctrl+Shift+N"));
        assert_eq!(libreoffice("PAGEDOWN_SHIFT").as_deref(), Some("Shift+PageDown"));
        assert_eq!(libreoffice("F5_MOD2").as_deref(), Some("Alt+F5"));
        assert_eq!(libreoffice("A_MOD3"), None);
    }
}
