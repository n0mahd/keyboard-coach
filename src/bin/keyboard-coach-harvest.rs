//! Build the shortcut index for installed applications, or capture the
//! shortcuts a running application publishes through accessibility.
//!
//! usage: keyboard-coach-harvest [--output PATH] [--summary] [--app ID]
//!        keyboard-coach-harvest observe --pid PID --class CLASS [--output PATH] [--print]

use std::env;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use keyboard_coach::atspi::{Bus, Limits};
use keyboard_coach::index::{self, Options};
use keyboard_coach::{observe, store};

const USAGE: &str = "usage: keyboard-coach-harvest [--output PATH] [--summary] [--app ID]\n       keyboard-coach-harvest observe --pid PID --class CLASS [--output PATH] [--print]";

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("observe") => observe_command(&args[1..]),
        _ => index_command(&args),
    }
}

fn index_command(args: &[String]) -> ExitCode {
    let mut output = store::data_dir().join("shortcuts.json");
    let mut summary = false;
    let mut only_app = None;
    let mut args = args.iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--output" => match args.next() {
                Some(path) => output = PathBuf::from(path),
                None => return usage(),
            },
            "--summary" => summary = true,
            "--app" => match args.next() {
                Some(id) => only_app = Some(id.clone()),
                None => return usage(),
            },
            _ => return usage(),
        }
    }

    let index = index::build(&Options::default());

    if let Some(id) = only_app {
        let Some(app) = index.apps.iter().find(|app| app.id == id) else {
            eprintln!("no installed app with id {id}");
            return ExitCode::FAILURE;
        };
        println!("{}", serde_json::to_string_pretty(app).expect("serializable"));
        return ExitCode::SUCCESS;
    }

    if summary {
        let mut rows: Vec<_> = index.apps.iter().collect();
        rows.sort_by(|a, b| b.shortcuts.len().cmp(&a.shortcuts.len()).then(a.id.cmp(&b.id)));
        for app in rows {
            let sources: Vec<String> = app.sources.iter().filter(|s| s.shortcuts > 0 || s.error.is_some()).map(|s| s.harvester.clone()).collect();
            println!("{:>4}  {:<40} {:<12} {}", app.shortcuts.len(), app.id, app.toolkit.as_deref().unwrap_or("-"), sources.join(","));
        }
        return ExitCode::SUCCESS;
    }

    if let Err(error) = store::write_json(&output, &index) {
        eprintln!("could not write {}: {error:#}", output.display());
        return ExitCode::FAILURE;
    }
    let total: usize = index.apps.iter().map(|app| app.shortcuts.len()).sum();
    let covered = index.apps.iter().filter(|app| !app.shortcuts.is_empty()).count();
    eprintln!("Indexed {total} shortcuts for {covered} of {} apps into {}", index.apps.len(), output.display());
    ExitCode::SUCCESS
}

fn observe_command(args: &[String]) -> ExitCode {
    let mut output = store::data_dir().join("observed.json");
    let mut pid = None;
    let mut class = None;
    let mut print = false;
    let mut args = args.iter();
    while let Some(arg) = args.next() {
        match (arg.as_str(), args.as_slice().first()) {
            ("--pid", Some(value)) => match value.parse::<u32>() {
                Ok(value) => pid = Some(value),
                Err(_) => return usage(),
            },
            ("--class", Some(value)) => class = Some(value.clone()),
            ("--output", Some(value)) => output = PathBuf::from(value),
            ("--print", _) => {
                print = true;
                continue;
            }
            _ => return usage(),
        }
        args.next();
    }
    let (Some(pid), Some(class)) = (pid, class) else { return usage() };

    let started = std::time::Instant::now();
    let observation = match Bus::connect().and_then(|bus| observe::observe_pid(&bus, pid, Limits::default())) {
        Ok(observation) => observation,
        Err(error) => {
            eprintln!("could not observe process {pid}: {error:#}");
            return ExitCode::FAILURE;
        }
    };
    if print {
        println!("{}", serde_json::to_string_pretty(&observation.shortcuts).expect("serializable"));
    }
    let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|elapsed| elapsed.as_secs()).unwrap_or_default();
    if let Err(error) = observe::record(&output, &class, &observation, now) {
        eprintln!("could not write {}: {error:#}", output.display());
        return ExitCode::FAILURE;
    }
    eprintln!(
        "Observed {} shortcuts in {} objects of {class} ({}) in {} ms",
        observation.shortcuts.len(),
        observation.nodes,
        observation.toolkit,
        started.elapsed().as_millis()
    );
    ExitCode::SUCCESS
}

fn usage() -> ExitCode {
    eprintln!("{USAGE}");
    ExitCode::from(2)
}
