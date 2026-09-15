//! Small helpers over quick-xml's streaming events.

use quick_xml::events::{BytesRef, BytesStart};
use quick_xml::XmlVersion;

/// An attribute's value with entities resolved.
pub fn attr(element: &BytesStart<'_>, key: &str) -> Option<String> {
    element
        .attributes()
        .flatten()
        .find(|attribute| attribute.key.as_ref() == key)
        .and_then(|attribute| attribute.normalized_value(XmlVersion::Implicit1_0).ok().map(|value| value.into_owned()))
}

/// Text for an entity or character reference that appeared inside content.
pub fn reference(reference: &BytesRef<'_>) -> String {
    if let Ok(Some(ch)) = reference.resolve_char_ref() {
        return ch.to_string();
    }
    match reference.as_ref() {
        "lt" => "<",
        "gt" => ">",
        "amp" => "&",
        "quot" => "\"",
        "apos" => "'",
        _ => "",
    }
    .to_string()
}
