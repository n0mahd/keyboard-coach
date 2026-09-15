//! Minimal ELF64 reader: section contents and dynamic library dependencies,
//! read with seeks so large binaries are never loaded whole.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

pub struct Section {
    pub name: String,
    pub kind: u32,
    pub offset: u64,
    pub size: u64,
    pub link: u32,
}

const SHT_NOBITS: u32 = 8;
const SHT_DYNAMIC: u32 = 6;
const DT_NEEDED: u64 = 1;
const MAX_SECTION: u64 = 256 * 1024 * 1024;

fn read_at(file: &mut File, offset: u64, len: usize) -> Option<Vec<u8>> {
    file.seek(SeekFrom::Start(offset)).ok()?;
    let mut buffer = vec![0; len];
    file.read_exact(&mut buffer).ok()?;
    Some(buffer)
}

fn u16_at(bytes: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([bytes[offset], bytes[offset + 1]])
}

fn u32_at(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap())
}

fn u64_at(bytes: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap())
}

pub struct Elf {
    file: File,
    pub sections: Vec<Section>,
}

impl Elf {
    /// Open a little-endian ELF64 file; other layouts are not needed here.
    pub fn open(path: &Path) -> Option<Elf> {
        let mut file = File::open(path).ok()?;
        let header = read_at(&mut file, 0, 64)?;
        if &header[..4] != b"\x7fELF" || header[4] != 2 || header[5] != 1 {
            return None;
        }
        let section_offset = u64_at(&header, 0x28);
        let entry_size = u16_at(&header, 0x3A) as usize;
        let count = u16_at(&header, 0x3C) as usize;
        let names_index = u16_at(&header, 0x3E) as usize;
        if entry_size < 64 || count == 0 || names_index >= count {
            return None;
        }
        let table = read_at(&mut file, section_offset, entry_size * count)?;
        let raw: Vec<(u32, u32, u64, u64, u32)> = (0..count)
            .map(|index| {
                let entry = &table[index * entry_size..];
                (u32_at(entry, 0), u32_at(entry, 4), u64_at(entry, 24), u64_at(entry, 32), u32_at(entry, 40))
            })
            .collect();
        let (_, _, names_offset, names_size, _) = raw[names_index];
        let names = read_at(&mut file, names_offset, names_size.min(MAX_SECTION) as usize)?;
        let sections = raw
            .into_iter()
            .map(|(name, kind, offset, size, link)| {
                let start = name as usize;
                let end = names[start.min(names.len())..].iter().position(|&b| b == 0).map_or(names.len(), |p| start + p);
                Section {
                    name: String::from_utf8_lossy(&names[start.min(names.len())..end]).into_owned(),
                    kind,
                    offset,
                    size,
                    link,
                }
            })
            .collect();
        Some(Elf { file, sections })
    }

    pub fn read(&mut self, section: &Section) -> Option<Vec<u8>> {
        if section.kind == SHT_NOBITS || section.size > MAX_SECTION {
            return None;
        }
        read_at(&mut self.file, section.offset, section.size as usize)
    }

    pub fn read_named(&mut self, predicate: impl Fn(&str) -> bool) -> Vec<(String, Vec<u8>)> {
        let wanted: Vec<(String, u32, u64, u64)> = self
            .sections
            .iter()
            .filter(|section| predicate(&section.name))
            .map(|section| (section.name.clone(), section.kind, section.offset, section.size))
            .collect();
        wanted
            .into_iter()
            .filter_map(|(name, kind, offset, size)| {
                let section = Section { name: name.clone(), kind, offset, size, link: 0 };
                self.read(&section).map(|bytes| (name, bytes))
            })
            .collect()
    }

    /// Shared libraries the file links against (`DT_NEEDED`).
    pub fn needed(&mut self) -> Vec<String> {
        let Some(dynamic) = self.sections.iter().position(|section| section.kind == SHT_DYNAMIC) else {
            return Vec::new();
        };
        let strings_index = self.sections[dynamic].link as usize;
        let (Some(dynamic_section), Some(strings_section)) = (self.sections.get(dynamic), self.sections.get(strings_index)) else {
            return Vec::new();
        };
        let (dyn_offset, dyn_size) = (dynamic_section.offset, dynamic_section.size);
        let (str_offset, str_size) = (strings_section.offset, strings_section.size);
        let (Some(entries), Some(strings)) = (
            read_at(&mut self.file, dyn_offset, dyn_size.min(MAX_SECTION) as usize),
            read_at(&mut self.file, str_offset, str_size.min(MAX_SECTION) as usize),
        ) else {
            return Vec::new();
        };
        entries
            .chunks_exact(16)
            .filter(|entry| u64_at(entry, 0) == DT_NEEDED)
            .filter_map(|entry| {
                let start = u64_at(entry, 8) as usize;
                let tail = strings.get(start..)?;
                let end = tail.iter().position(|&b| b == 0)?;
                Some(String::from_utf8_lossy(&tail[..end]).into_owned())
            })
            .collect()
    }
}

/// A coarse toolkit label from linked libraries and files beside the binary.
pub fn toolkit(path: &Path, needed: &[String]) -> Option<&'static str> {
    let has = |prefix: &str| needed.iter().any(|lib| lib.starts_with(prefix));
    let dir = path.parent();
    let beside = |name: &str| dir.is_some_and(|dir| dir.join(name).exists());
    if beside("resources/app.asar") || beside("resources/electron.asar") {
        Some("electron")
    } else if beside("chrome_100_percent.pak") || beside("resources.pak") {
        Some("chromium")
    } else if has("libflutter_linux_gtk") {
        Some("flutter")
    } else if has("libadwaita-1") {
        Some("libadwaita")
    } else if has("libgtk-4") {
        Some("gtk4")
    } else if has("libgtk-3") {
        Some("gtk3")
    } else if has("libKF6XmlGui") || has("libKF5XmlGui") {
        Some("kde")
    } else if has("libQt6Quick") || has("libQt5Quick") {
        Some("qt-quick")
    } else if has("libQt6Widgets") || has("libQt5Widgets") {
        Some("qt-widgets")
    } else if needed.iter().any(|lib| lib.starts_with("libsofficeapp") || lib.starts_with("libuno_sal")) {
        Some("libreoffice")
    } else {
        None
    }
}
