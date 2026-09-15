//! Read GResource bundles: the GVDB files GTK applications compile their UI
//! definitions into, either embedded in the executable or shipped as
//! `.gresource` files.
//!
//! Format reference: glib's gvdb-format.h and gresource.c. A bundle is a GVDB
//! hash table whose items are resource paths; each file item is a serialized
//! GVariant of type `(uuay)`: uncompressed size, flags, and data.

use std::io::Read;

const SIGNATURE: &[u8; 8] = b"GVariant";
const HEADER_SIZE: usize = 24;
const HASH_ITEM_SIZE: usize = 24;
const RESOURCE_COMPRESSED: u32 = 1;
/// Resources larger than this are not UI definitions and are skipped.
const MAX_RESOURCE_SIZE: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Resource {
    pub path: String,
    pub data: Vec<u8>,
}

fn u32_at(bytes: &[u8], offset: usize) -> Option<u32> {
    Some(u32::from_le_bytes(bytes.get(offset..offset + 4)?.try_into().ok()?))
}

fn u16_at(bytes: &[u8], offset: usize) -> Option<u16> {
    Some(u16::from_le_bytes(bytes.get(offset..offset + 2)?.try_into().ok()?))
}

struct Item {
    parent: u32,
    key: Vec<u8>,
    kind: u8,
    start: usize,
    end: usize,
}

/// Parse one GVDB file that begins at the start of `bytes`. `bytes` may extend
/// past the end of the file, as when the bundle is embedded in a binary.
pub fn parse(bytes: &[u8]) -> Option<Vec<Resource>> {
    if bytes.get(..8)? != SIGNATURE || u32_at(bytes, 8)? != 0 {
        return None;
    }
    let root_start = u32_at(bytes, 16)? as usize;
    let root_end = u32_at(bytes, 20)? as usize;
    if root_start < HEADER_SIZE || root_end <= root_start || root_end > bytes.len() {
        return None;
    }
    let table = &bytes[root_start..root_end];
    let bloom_words = (u32_at(table, 0)? & ((1 << 27) - 1)) as usize;
    let buckets = u32_at(table, 4)? as usize;
    let items_offset = 8usize.checked_add(bloom_words.checked_mul(4)?)?.checked_add(buckets.checked_mul(4)?)?;
    if items_offset > table.len() || (table.len() - items_offset) % HASH_ITEM_SIZE != 0 {
        return None;
    }
    let count = (table.len() - items_offset) / HASH_ITEM_SIZE;
    let mut items = Vec::with_capacity(count);
    for index in 0..count {
        let item = items_offset + index * HASH_ITEM_SIZE;
        let key_start = u32_at(table, item + 8)? as usize;
        let key_size = u16_at(table, item + 12)? as usize;
        items.push(Item {
            parent: u32_at(table, item + 4)?,
            key: bytes.get(key_start..key_start.checked_add(key_size)?)?.to_vec(),
            kind: *table.get(item + 14)?,
            start: u32_at(table, item + 16)? as usize,
            end: u32_at(table, item + 20)? as usize,
        });
    }

    let mut resources = Vec::new();
    for (index, item) in items.iter().enumerate() {
        if item.kind != b'v' {
            continue;
        }
        let Some(path) = full_key(&items, index) else { continue };
        let Some(value) = bytes.get(item.start..item.end) else { continue };
        if let Some(data) = resource_data(value) {
            resources.push(Resource { path, data });
        }
    }
    (!resources.is_empty() || !items.is_empty()).then_some(resources)
}

fn full_key(items: &[Item], index: usize) -> Option<String> {
    let mut parts = Vec::new();
    let mut current = index;
    for _ in 0..64 {
        let item = items.get(current)?;
        parts.push(item.key.as_slice());
        if item.parent == u32::MAX {
            let joined: Vec<u8> = parts.into_iter().rev().flatten().copied().collect();
            return String::from_utf8(joined).ok();
        }
        current = item.parent as usize;
    }
    None
}

/// Decode a serialized `(uuay)` variant: the value, a NUL, then the type string.
fn resource_data(value: &[u8]) -> Option<Vec<u8>> {
    let type_start = value.iter().rposition(|&byte| byte == 0)?;
    if &value[type_start + 1..] != b"(uuay)" {
        return None;
    }
    let size = u32_at(value, 0)? as usize;
    let flags = u32_at(value, 4)?;
    let payload = value.get(8..type_start)?;
    if size > MAX_RESOURCE_SIZE {
        return None;
    }
    if flags & RESOURCE_COMPRESSED != 0 {
        let mut decoded = Vec::with_capacity(size);
        flate2::read::ZlibDecoder::new(payload).take(MAX_RESOURCE_SIZE as u64).read_to_end(&mut decoded).ok()?;
        Some(decoded)
    } else {
        // glib-compile-resources appends a NUL terminator after the data.
        Some(payload.get(..size.min(payload.len()))?.to_vec())
    }
}

/// Find every GResource bundle inside a file: a `.gresource` file, or an
/// executable or library that embeds bundles in its data sections.
pub fn scan(bytes: &[u8]) -> Vec<Resource> {
    let mut resources = Vec::new();
    let mut offset = 0;
    while let Some(found) = find(&bytes[offset..], SIGNATURE) {
        let start = offset + found;
        if let Some(bundle) = parse(&bytes[start..]) {
            resources.extend(bundle);
        }
        offset = start + SIGNATURE.len();
    }
    resources
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|window| window == needle)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::io::Write;

    /// Build a GVDB bundle the way glib-compile-resources lays it out: a root
    /// directory item, then one file item per resource.
    pub fn bundle(files: &[(&str, &[u8], bool)]) -> Vec<u8> {
        let mut data = Vec::new();
        let mut items = Vec::new();
        // Keys and values follow the header and hash table, whose size is
        // known up front from the item count.
        let item_count = files.len() + 1;
        let table_len = 8 + 4 + HASH_ITEM_SIZE * item_count; // one bucket, no bloom
        let mut body = Vec::new();
        let base = HEADER_SIZE + table_len;
        let push = |bytes: &[u8], body: &mut Vec<u8>| {
            let start = base + body.len();
            body.extend_from_slice(bytes);
            (start, base + body.len())
        };
        let (root_key_start, _) = push(b"/", &mut body);
        items.push((u32::MAX, root_key_start, 1u16, b'L', 0usize, 0usize));
        for (name, content, compress) in files {
            let (key_start, _) = push(name.as_bytes(), &mut body);
            let payload = if *compress {
                let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
                encoder.write_all(content).unwrap();
                encoder.finish().unwrap()
            } else {
                let mut raw = content.to_vec();
                raw.push(0);
                raw
            };
            let mut value = Vec::new();
            value.extend_from_slice(&(content.len() as u32).to_le_bytes());
            value.extend_from_slice(&(if *compress { RESOURCE_COMPRESSED } else { 0 }).to_le_bytes());
            value.extend_from_slice(&payload);
            value.push(0);
            value.extend_from_slice(b"(uuay)");
            let (value_start, value_end) = push(&value, &mut body);
            items.push((0, key_start, name.len() as u16, b'v', value_start, value_end));
        }
        data.extend_from_slice(SIGNATURE);
        data.extend_from_slice(&0u32.to_le_bytes());
        data.extend_from_slice(&0u32.to_le_bytes());
        data.extend_from_slice(&(HEADER_SIZE as u32).to_le_bytes());
        data.extend_from_slice(&((HEADER_SIZE + table_len) as u32).to_le_bytes());
        data.extend_from_slice(&0u32.to_le_bytes());
        data.extend_from_slice(&1u32.to_le_bytes());
        data.extend_from_slice(&0u32.to_le_bytes());
        for (parent, key_start, key_size, kind, start, end) in items {
            data.extend_from_slice(&0u32.to_le_bytes());
            data.extend_from_slice(&parent.to_le_bytes());
            data.extend_from_slice(&(key_start as u32).to_le_bytes());
            data.extend_from_slice(&key_size.to_le_bytes());
            data.push(kind);
            data.push(0);
            data.extend_from_slice(&(start as u32).to_le_bytes());
            data.extend_from_slice(&(end as u32).to_le_bytes());
        }
        data.extend_from_slice(&body);
        data
    }

    #[test]
    fn reads_plain_and_compressed_resources_embedded_in_other_bytes() {
        let gvdb = bundle(&[
            ("gtk/help-overlay.ui", b"<interface/>", false),
            ("ui/menus.ui", b"<interface><menu/></interface>", true),
        ]);
        let mut binary = b"\x7fELF padding GVariant-not-a-bundle ".to_vec();
        binary.extend_from_slice(&gvdb);
        binary.extend_from_slice(b"trailing section data");

        let resources = scan(&binary);

        assert_eq!(resources.len(), 2);
        assert_eq!(resources[0].path, "/gtk/help-overlay.ui");
        assert_eq!(resources[0].data, b"<interface/>");
        assert_eq!(resources[1].data, b"<interface><menu/></interface>");
    }

    #[test]
    fn rejects_truncated_bundles() {
        let gvdb = bundle(&[("a.ui", b"<interface/>", false)]);
        assert!(parse(&gvdb[..gvdb.len() / 2]).map_or(true, |resources| resources.is_empty()));
    }
}
