// kicad-hub desktop app: the core behind the page in ../ui.
//
// It remembers the project, watches its folder and, once a save has settled, runs the
// engine's quick check (`kicad_hub.py status`, with the Python bundled with the app) and
// sends the page what the engine wrote to .kicad-hub/status.json. No web server, no port:
// the page talks to this process only (Tauri commands and one "state" event).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use notify::{RecommendedWatcher, RecursiveMode, Watcher};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, State, WebviewWindow};
use tauri_plugin_dialog::DialogExt;

/// The engine's output folder, at the top of the project's repository (as the plugin's).
const OUT: &str = ".kicad-hub";
/// The files whose saving means "check again". KiCad also writes backups and the
/// .kicad_prl (window layout) on save: those don't change the design.
const WATCHED: [&str; 4] = ["kicad_pcb", "kicad_sch", "kicad_pro", "kicad_dru"];
/// How long the files must stay quiet before checking: a save writes several files.
const SETTLE: Duration = Duration::from_millis(1500);
const SIDE_WIDTH: f64 = 400.0;
const FULL: (f64, f64) = (1280.0, 860.0);
const THEMES: [&str; 5] = ["green", "blue", "purple", "red", "black"];

#[cfg(windows)]
const NO_WINDOW: u32 = 0x0800_0000; // CREATE_NO_WINDOW: kicad-cli and git are console programs

#[derive(Serialize, Deserialize, Clone)]
#[serde(default)]
struct Settings {
    project: Option<PathBuf>,
    theme: String,
    mode: String,
}

impl Default for Settings {
    fn default() -> Self {
        Settings { project: None, theme: "green".into(), mode: "full".into() }
    }
}

#[derive(Serialize, Clone, Debug)]
struct Project {
    dir: String,
    work: String,
    name: String,
    #[serde(skip)]
    rel: String,
}

#[derive(Serialize, Clone)]
struct Features {
    show_in_kicad: bool,
}

/// What the page shows.
#[derive(Serialize, Clone)]
struct View {
    project: Option<Project>,
    theme: String,
    mode: String,
    checking: bool,
    problem: Option<String>,
    status: Option<Value>,
    features: Features,
    ordering: Option<String>,
    order: Option<Value>,
}

#[derive(Default)]
struct Inner {
    settings: Settings,
    project: Option<Project>,
    checking: bool,
    /// The last change of a watched file not checked yet.
    changed_at: Option<Instant>,
    problem: Option<String>,
    watcher: Option<RecommendedWatcher>,
    /// Prepare the order is running: the engine's step now ("Starting" first).
    ordering: Option<String>,
}

type Shared = Arc<Mutex<Inner>>;

// --- settings -------------------------------------------------------------------------------

fn settings_file(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("settings.json"))
}

fn load_settings(app: &AppHandle) -> Settings {
    settings_file(app)
        .and_then(|f| fs::read_to_string(f).ok())
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

fn save_settings(app: &AppHandle, s: &Settings) {
    if let Some(file) = settings_file(app) {
        if let Some(dir) = file.parent() {
            let _ = fs::create_dir_all(dir);
        }
        if let Ok(text) = serde_json::to_string_pretty(s) {
            let tmp = file.with_extension("json.tmp");
            if fs::write(&tmp, text).is_ok() {
                let _ = fs::rename(&tmp, &file);
            }
        }
    }
}

// --- the project ----------------------------------------------------------------------------

fn quiet(cmd: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(NO_WINDOW);
    }
    cmd
}

/// The top of the git repository the folder is in, else the folder itself (as the plugin).
fn work_folder(dir: &Path) -> PathBuf {
    let out = quiet(Command::new("git").arg("-C").arg(dir).args(["rev-parse", "--show-toplevel"]))
        .stdin(Stdio::null())
        .output();
    if let Ok(out) = out {
        let top = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if out.status.success() && !top.is_empty() && Path::new(&top).is_dir() {
            return PathBuf::from(top);
        }
    }
    dir.to_path_buf()
}

/// The project in a folder the user picked: the folder must hold one .kicad_pro.
fn resolve(dir: &Path) -> Result<Project, String> {
    let entries = fs::read_dir(dir).map_err(|e| format!("Can't open {}: {e}", dir.display()))?;
    let mut pros: Vec<PathBuf> = entries
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().map_or(false, |x| x == "kicad_pro"))
        .collect();
    pros.sort();
    let pro = match pros.len() {
        0 => return Err(format!("There is no KiCad project (.kicad_pro file) in {}.", dir.display())),
        1 => pros.remove(0),
        _ => return Err(format!("{} holds more than one KiCad project: choose the folder of one.", dir.display())),
    };
    let name = pro.file_stem().unwrap_or_default().to_string_lossy().to_string();
    let dir = fs::canonicalize(dir).unwrap_or_else(|_| dir.to_path_buf());
    let work = work_folder(&dir);
    let work = fs::canonicalize(&work).unwrap_or(work);
    let rel = dir.strip_prefix(&work).map(|r| r.to_string_lossy().replace('\\', "/")).unwrap_or_default();
    Ok(Project { dir: plain(&dir), work: plain(&work), name, rel })
}

/// A path as people read it (no \\?\ prefix from canonicalize on Windows).
fn plain(p: &Path) -> String {
    let s = p.to_string_lossy().to_string();
    s.strip_prefix(r"\\?\").map(str::to_string).unwrap_or(s)
}

/// Keep .kicad-hub/ out of git: a line in .git/info/exclude (local, never committed),
/// unless .gitignore already lists it. The same rule as the plugin's.
fn ignore_outputs(work: &Path) {
    let git = work.join(".git");
    if !git.is_dir() {
        return;
    }
    let info = git.join("info");
    for f in [work.join(".gitignore"), info.join("exclude")] {
        if let Ok(text) = fs::read_to_string(&f) {
            if text.lines().any(|l| l.trim().trim_matches('/') == OUT) {
                return;
            }
        }
    }
    let _ = fs::create_dir_all(&info);
    let file = info.join("exclude");
    let mut text = fs::read_to_string(&file).unwrap_or_default();
    text.push_str(&format!("\n# kicad-hub's local outputs (the desktop app)\n{OUT}/\n"));
    let _ = fs::write(file, text);
}

fn read_json(p: &Project, name: &str) -> Option<Value> {
    let text = fs::read_to_string(Path::new(&p.work).join(OUT).join(name)).ok()?;
    serde_json::from_str(&text).ok()
}

fn read_status(p: &Project) -> Option<Value> {
    read_json(p, "status.json")
}

fn view(inner: &Inner) -> View {
    View {
        project: inner.project.clone(),
        theme: inner.settings.theme.clone(),
        mode: inner.settings.mode.clone(),
        checking: inner.checking,
        problem: inner.problem.clone(),
        status: inner.project.as_ref().and_then(read_status),
        features: Features { show_in_kicad: true },
        ordering: inner.ordering.clone(),
        order: inner.project.as_ref().and_then(|p| read_json(p, "order.json")),
    }
}

fn push(app: &AppHandle, shared: &Shared) {
    let v = view(&shared.lock().unwrap());
    let _ = app.emit("state", v);
}

// --- watching -------------------------------------------------------------------------------

/// Does this change call for a check? A watched file, not in a hidden folder (.git,
/// .kicad-hub, and .kicad-hub-base, where the engine checks out main to compare the BOM)
/// nor in KiCad's backups folder (<name>-backups). Only the part below the project folder
/// counts: the folder itself may be anywhere.
fn matters(path: &Path, root: &Path) -> bool {
    let watched = path.extension().and_then(|x| x.to_str()).map_or(false, |x| WATCHED.contains(&x));
    let inside = path.strip_prefix(root).unwrap_or(path);
    watched
        && !inside.components().any(|c| {
            let c = c.as_os_str().to_string_lossy();
            c.starts_with('.') || c.ends_with("-backups")
        })
}

fn watch(shared: &Shared, dir: &Path) -> Result<RecommendedWatcher, String> {
    let s = shared.clone();
    let root = dir.to_path_buf();
    let mut w = notify::recommended_watcher(move |res: notify::Result<notify::Event>| {
        if let Ok(event) = res {
            if event.kind.is_access() || event.kind.is_other() {
                return;
            }
            if event.paths.iter().any(|p| matters(p, &root)) {
                s.lock().unwrap().changed_at = Some(Instant::now());
            }
        }
    })
    .map_err(|e| format!("Can't watch the project folder: {e}"))?;
    w.watch(dir, RecursiveMode::Recursive).map_err(|e| format!("Can't watch the project folder: {e}"))?;
    Ok(w)
}

/// Every few tenths of a second: start a check once a save has settled and none runs.
fn ticker(app: AppHandle, shared: Shared) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_millis(300));
        let due = {
            let mut i = shared.lock().unwrap();
            match i.changed_at {
                Some(t) if !i.checking && i.ordering.is_none() && i.project.is_some() && t.elapsed() >= SETTLE => {
                    i.changed_at = None;
                    true
                }
                _ => false,
            }
        };
        if due {
            check(&app, &shared);
        }
    });
}

fn open_project(app: &AppHandle, shared: &Shared, dir: &Path) -> Result<(), String> {
    let project = resolve(dir)?;
    ignore_outputs(Path::new(&project.work));
    let watcher = watch(shared, Path::new(&project.dir))?;
    {
        let mut i = shared.lock().unwrap();
        i.settings.project = Some(PathBuf::from(&project.dir));
        i.project = Some(project);
        i.watcher = Some(watcher);
        i.problem = None;
        i.changed_at = None;
        save_settings(app, &i.settings);
    }
    check(app, shared);
    Ok(())
}

// --- the engine ------------------------------------------------------------------------------

/// The Python that runs the engine: $KICAD_HUB_PYTHON, the one bundled with the app
/// (resources/python), else python3 / python on the PATH (a build from the repository).
fn python(app: &AppHandle) -> PathBuf {
    if let Some(p) = std::env::var_os("KICAD_HUB_PYTHON") {
        return PathBuf::from(p);
    }
    if let Ok(res) = app.path().resource_dir() {
        for c in [res.join("python").join("python.exe"), res.join("python").join("bin").join("python3")] {
            if c.is_file() {
                return c;
            }
        }
    }
    PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
}

/// kicad_hub.py: $KICAD_HUB_ENGINE, the copy bundled with the app, else the repository's.
fn engine(app: &AppHandle) -> PathBuf {
    if let Some(p) = std::env::var_os("KICAD_HUB_ENGINE") {
        return PathBuf::from(p);
    }
    if let Ok(res) = app.path().resource_dir() {
        let c = res.join("engine").join("scripts").join("kicad_hub.py");
        if c.is_file() {
            return c;
        }
    }
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("scripts").join("kicad_hub.py")
}

/// Run the quick check in the background; the page hears when it starts and ends.
fn check(app: &AppHandle, shared: &Shared) {
    let project = {
        let mut i = shared.lock().unwrap();
        if i.checking || i.ordering.is_some() {
            i.changed_at.get_or_insert(Instant::now()); // once this one ends: they share .kicad-hub-base
            return;
        }
        match i.project.clone() {
            Some(p) => {
                i.checking = true;
                p
            }
            None => return,
        }
    };
    push(app, shared);
    let (app, shared) = (app.clone(), shared.clone());
    let (python, engine) = (python(&app), engine(&app));
    thread::spawn(move || {
        let out = Path::new(&project.work).join(OUT);
        let _ = fs::create_dir_all(&out);
        let log = fs::File::create(out.join("app.log"));
        let mut cmd = Command::new(&python);
        cmd.arg(&engine).args(["status", "--out", OUT, "--project-dir"]).arg(&project.rel);
        cmd.current_dir(&project.work).env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8").stdin(Stdio::null());
        if let Ok(log) = log {
            if let Ok(err) = log.try_clone() {
                cmd.stdout(log).stderr(err);
            }
        }
        let problem = match quiet(&mut cmd).status() {
            Ok(_) => None, // a failed check still writes status.json, with what went wrong
            Err(e) => Some(format!("{} could not start: {e}", python.display())),
        };
        {
            let mut i = shared.lock().unwrap();
            i.checking = false;
            i.problem = problem;
        }
        push(&app, &shared);
    });
}

/// Prepare the order: the engine's whole run but the portal (datasheet links, prices and
/// stock, the fab package, requirements, report), ~3 minutes. The page hears each step;
/// at the end it gets order.json and a quick check, so links found broken join the list.
fn prepare(app: &AppHandle, shared: &Shared) -> Result<(), String> {
    let project = {
        let mut i = shared.lock().unwrap();
        if i.ordering.is_some() {
            return Ok(());
        }
        if i.checking {
            return Err("Wait for the check to finish, then try again.".into());
        }
        let p = i.project.clone().ok_or("Choose a project first.")?;
        i.ordering = Some("Starting".into());
        p
    };
    push(app, shared);
    let (app, shared) = (app.clone(), shared.clone());
    let (python, engine) = (python(&app), engine(&app));
    thread::spawn(move || {
        use std::io::{BufRead, BufReader, Write};
        let out = Path::new(&project.work).join(OUT);
        let _ = fs::create_dir_all(&out);
        let mut log = fs::File::create(out.join("order.log")).ok();
        let mut cmd = Command::new(&python);
        cmd.arg(&engine).args(["run", "--skip", "portal", "--out", OUT, "--project-dir"]).arg(&project.rel);
        cmd.current_dir(&project.work).env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8");
        cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let problem = match quiet(&mut cmd).spawn() {
            Ok(mut child) => {
                // kicad-cli's messages come on stderr: into the log, so the pipe never fills up
                let err = child.stderr.take().map(|e| {
                    let path = out.join("order-errors.log");
                    thread::spawn(move || {
                        if let Ok(mut f) = fs::File::create(path) {
                            let _ = std::io::copy(&mut BufReader::new(e), &mut f);
                        }
                    })
                });
                if let Some(stdout) = child.stdout.take() {
                    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                        if let Some(f) = log.as_mut() {
                            let _ = writeln!(f, "{line}");
                        }
                        if let Some(step) = line.strip_prefix("== ") {
                            shared.lock().unwrap().ordering = Some(step.trim().to_string());
                            push(&app, &shared);
                        }
                    }
                }
                let code = child.wait().map(|s| s.success()).unwrap_or(false);
                if let Some(t) = err {
                    let _ = t.join();
                }
                if code { None } else {
                    Some("Preparing the order stopped. Its messages are in .kicad-hub/order.log and order-errors.log.".to_string())
                }
            }
            Err(e) => Some(format!("{} could not start: {e}", python.display())),
        };
        {
            let mut i = shared.lock().unwrap();
            i.ordering = None;
            i.problem = problem;
        }
        push(&app, &shared);
        check(&app, &shared);
    });
    Ok(())
}

/// Show a file in the system's file manager (the fab package's zip).
fn reveal(path: &Path) -> Result<(), String> {
    let mut cmd = if cfg!(windows) {
        let mut c = Command::new("explorer");
        c.arg(format!("/select,{}", path.display()));
        c
    } else if cfg!(target_os = "macos") {
        let mut c = Command::new("open");
        c.arg("-R").arg(path);
        c
    } else {
        let mut c = Command::new("xdg-open");
        c.arg(path.parent().unwrap_or(path));
        c
    };
    cmd.spawn().map(|_| ()).map_err(|e| format!("Can't open the folder: {e}"))
}

// --- the window ------------------------------------------------------------------------------

/// Full window, or the strip down the right-hand side of the screen, beside KiCad.
fn apply_mode(window: &WebviewWindow, mode: &str) {
    let Ok(Some(monitor)) = window.current_monitor() else { return };
    let scale = monitor.scale_factor();
    let area = monitor.work_area();
    let (pos, size) = (area.position, area.size);
    if mode == "side" {
        let w = (SIDE_WIDTH * scale).round() as u32;
        let _ = window.unmaximize();
        let _ = window.set_size(PhysicalSize::new(w.min(size.width), size.height));
        let _ = window.set_position(PhysicalPosition::new(pos.x + size.width as i32 - w as i32, pos.y));
    } else {
        let w = ((FULL.0 * scale).round() as u32).min(size.width);
        let h = ((FULL.1 * scale).round() as u32).min(size.height);
        let _ = window.set_size(PhysicalSize::new(w, h));
        let _ = window.center();
    }
}

// --- commands --------------------------------------------------------------------------------

#[tauri::command]
fn get_state(shared: State<Shared>) -> View {
    view(&shared.lock().unwrap())
}

#[tauri::command]
async fn choose_project(app: AppHandle, shared: State<'_, Shared>) -> Result<View, String> {
    let picked = app.dialog().file().set_title("Choose your KiCad project folder").blocking_pick_folder();
    let Some(folder) = picked else { return Ok(view(&shared.lock().unwrap())) }; // cancelled
    let dir = folder.into_path().map_err(|e| e.to_string())?;
    let shared: Shared = shared.inner().clone();
    if let Err(e) = open_project(&app, &shared, &dir) {
        shared.lock().unwrap().problem = Some(e);
    }
    let v = view(&shared.lock().unwrap());
    Ok(v)
}

#[tauri::command]
fn check_now(app: AppHandle, shared: State<Shared>) -> View {
    let shared: Shared = shared.inner().clone();
    check(&app, &shared);
    let v = view(&shared.lock().unwrap());
    v
}

#[tauri::command]
fn prepare_order(app: AppHandle, shared: State<Shared>) -> Result<View, String> {
    let shared: Shared = shared.inner().clone();
    prepare(&app, &shared)?;
    let v = view(&shared.lock().unwrap());
    Ok(v)
}

#[tauri::command]
fn open_package(shared: State<Shared>) -> Result<(), String> {
    let project = shared.lock().unwrap().project.clone().ok_or("Choose a project first.")?;
    let order = read_json(&project, "order.json").ok_or("Prepare the order first.")?;
    let zip = order.pointer("/package/zip").and_then(Value::as_str).ok_or("There is no fab package yet.")?;
    let path = Path::new(&project.work).join(zip);
    if !path.is_file() {
        return Err("The fab package is gone: prepare the order again.".into());
    }
    reveal(&path)
}

#[tauri::command]
fn set_theme(app: AppHandle, shared: State<Shared>, theme: String) -> View {
    let mut i = shared.lock().unwrap();
    if THEMES.contains(&theme.as_str()) {
        i.settings.theme = theme;
        save_settings(&app, &i.settings);
    }
    view(&i)
}

#[tauri::command]
fn set_mode(app: AppHandle, window: WebviewWindow, shared: State<Shared>, mode: String) -> View {
    let mode = if mode == "side" { "side" } else { "full" }.to_string();
    apply_mode(&window, &mode);
    let mut i = shared.lock().unwrap();
    i.settings.mode = mode;
    save_settings(&app, &i.settings);
    view(&i)
}

/// What Show in KiCad answers the page: whether it worked, and a sentence.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
struct Shown {
    ok: bool,
    message: String,
}

/// The helper that talks to KiCad: the copy bundled with the app, else the repository's.
fn helper(app: &AppHandle) -> PathBuf {
    if let Ok(res) = app.path().resource_dir() {
        let c = res.join("helper").join("show_in_kicad.py");
        if c.is_file() {
            return c;
        }
    }
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("python").join("show_in_kicad.py")
}

/// The request for one row of status.json: its items, and its parts' footprints.
fn show_request(status: &Value, id: &str, project: &Project) -> Option<Value> {
    let row = status.get("items")?.as_array()?.iter().find(|r| r.get("id").and_then(Value::as_str) == Some(id))?;
    let list = |key: &str| row.get(key).cloned().unwrap_or_else(|| Value::Array(vec![]));
    Some(serde_json::json!({
        "ids": list("select"),
        "footprints": list("footprints"),
        "project_dir": project.dir,
        "board": format!("{}.kicad_pcb", project.name),
    }))
}

#[tauri::command]
async fn show_in_kicad(app: AppHandle, shared: State<'_, Shared>, id: String) -> Result<Shown, String> {
    let project = shared.lock().unwrap().project.clone().ok_or("Choose a project first.")?;
    let status = read_status(&project).ok_or("Wait for the check to finish.")?;
    let request = show_request(&status, &id, &project).ok_or("That row is gone: the list has changed.")?;
    let mut cmd = Command::new(python(&app));
    cmd.arg(helper(&app)).env("PYTHONUTF8", "1").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    let mut child = quiet(&mut cmd).spawn().map_err(|e| format!("Show in KiCad could not start: {e}"))?;
    if let Some(mut stdin) = child.stdin.take() {
        use std::io::Write;
        let _ = stdin.write_all(request.to_string().as_bytes());
    }
    let out = child.wait_with_output().map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str::<Shown>(text.trim().lines().last().unwrap_or(""))
        .map_err(|_| "Show in KiCad stopped without an answer.".to_string())
}

/// `--project <folder>` (or `--project=<folder>`): the KiCad plugin's button opens the app
/// on the project open in KiCad.
fn project_arg(argv: &[String]) -> Option<PathBuf> {
    let mut args = argv.iter().skip(1);
    while let Some(a) = args.next() {
        if a == "--project" {
            return args.next().map(PathBuf::from);
        }
        if let Some(v) = a.strip_prefix("--project=") {
            return Some(PathBuf::from(v));
        }
    }
    None
}

fn same_folder(a: &Path, b: &Path) -> bool {
    match (fs::canonicalize(a), fs::canonicalize(b)) {
        (Ok(a), Ok(b)) => a == b,
        _ => a == b,
    }
}

/// Bring the window to the front (from the KiCad plugin's button, or a second launch).
fn to_front(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

/// A second launch (the plugin's button): no second window, this one comes forward and,
/// when it names another project, switches to it.
fn second_launch(app: &AppHandle, argv: Vec<String>) {
    to_front(app);
    let Some(dir) = project_arg(&argv) else { return };
    let shared: Shared = app.state::<Shared>().inner().clone();
    let current = shared.lock().unwrap().project.as_ref().map(|p| PathBuf::from(&p.dir));
    if current.as_deref().map_or(false, |c| same_folder(c, &dir)) {
        return;
    }
    if let Err(e) = open_project(app, &shared, &dir) {
        shared.lock().unwrap().problem = Some(e);
    }
    push(app, &shared);
}

fn main() {
    let shared: Shared = Arc::new(Mutex::new(Inner::default()));
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| second_launch(app, argv)))
        .plugin(tauri_plugin_dialog::init())
        .manage(shared.clone())
        .setup(move |app| {
            let handle = app.handle().clone();
            let settings = load_settings(&handle);
            let asked = project_arg(&std::env::args().collect::<Vec<_>>());
            let (project, mode) = (asked.or(settings.project.clone()), settings.mode.clone());
            shared.lock().unwrap().settings = settings;
            if let Some(window) = app.get_webview_window("main") {
                if mode == "side" {
                    apply_mode(&window, "side");
                }
            }
            if let Some(dir) = project {
                if let Err(e) = open_project(&handle, &shared, &dir) {
                    shared.lock().unwrap().problem = Some(e);
                }
            }
            ticker(handle, shared.clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_state, choose_project, check_now, prepare_order, open_package, set_theme,
                                                 set_mode, show_in_kicad])
        .run(tauri::generate_context!())
        .expect("kicad-hub could not start");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn what_a_save_is() {
        let root = Path::new("/boards/riego");
        assert!(matters(Path::new("/boards/riego/hw/board.kicad_pcb"), root));
        assert!(matters(Path::new("/boards/riego/hw/power.kicad_sch"), root));
        assert!(matters(Path::new("/boards/riego/board.kicad_dru"), root));
        assert!(!matters(Path::new("/boards/riego/board.kicad_prl"), root)); // window layout
        assert!(!matters(Path::new("/boards/riego/hw/board-backups/board.kicad_pcb"), root));
        assert!(!matters(Path::new("/boards/riego/.kicad-hub-base/hw/board.kicad_sch"), root));
        assert!(!matters(Path::new("/boards/riego/.kicad-hub/quick/drc.json"), root));
        assert!(!matters(Path::new("/boards/riego/.git/index"), root));
        // the project may itself live under a hidden folder or one named like a backup
        assert!(matters(Path::new("/home/me/.boards/x-backups/riego/board.kicad_pcb"),
                        Path::new("/home/me/.boards/x-backups/riego")));
    }

    #[test]
    fn the_project_from_the_command_line() {
        let args = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(project_arg(&args(&["kicad-hub", "--project", "C:/b/riego"])), Some(PathBuf::from("C:/b/riego")));
        assert_eq!(project_arg(&args(&["kicad-hub", "--project=/b/x"])), Some(PathBuf::from("/b/x")));
        assert_eq!(project_arg(&args(&["kicad-hub"])), None);
        assert_eq!(project_arg(&args(&["kicad-hub", "--project"])), None);
    }

    #[test]
    fn the_request_for_a_row() {
        let status = serde_json::json!({"items": [
            {"id": "a1", "select": ["u1", "u2"], "footprints": ["f1"]},
            {"id": "b2", "select": [], "footprints": ["f9"]}]});
        let p = Project { dir: "/b/riego".into(), work: "/b".into(), name: "riego".into(), rel: "riego".into() };
        let r = show_request(&status, "a1", &p).unwrap();
        assert_eq!(r["ids"], serde_json::json!(["u1", "u2"]));
        assert_eq!(r["footprints"], serde_json::json!(["f1"]));
        assert_eq!(r["board"], "riego.kicad_pcb");
        assert!(show_request(&status, "zz", &p).is_none());
    }

    #[test]
    fn a_folder_with_one_project() {
        let tmp = std::env::temp_dir().join(format!("kicad-hub-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        assert!(resolve(&tmp).unwrap_err().contains("no KiCad project"));
        fs::write(tmp.join("riego.kicad_pro"), "{}").unwrap();
        let p = resolve(&tmp).unwrap();
        assert_eq!(p.name, "riego");
        fs::write(tmp.join("other.kicad_pro"), "{}").unwrap();
        assert!(resolve(&tmp).unwrap_err().contains("more than one"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn outputs_kept_out_of_git_once() {
        let tmp = std::env::temp_dir().join(format!("kicad-hub-git-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(tmp.join(".git")).unwrap();
        ignore_outputs(&tmp);
        ignore_outputs(&tmp);
        let text = fs::read_to_string(tmp.join(".git/info/exclude")).unwrap();
        assert_eq!(text.matches(".kicad-hub/").count(), 1);
        let _ = fs::remove_dir_all(&tmp);
    }
}
