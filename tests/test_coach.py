#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("coach", ROOT / "scripts/coach.py")
coach = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(coach)


class FakeAtspi:
    class CoordType:
        WINDOW = "window"
        SCREEN = "screen"

    class StateType:
        SHOWING = "showing"
        ACTIVE = "active"
        SELECTED = "selected"
        FOCUSED = "focused"


class Extents:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


class States:
    def __init__(self, states):
        self.states = set(states)

    def contains(self, state):
        return state in self.states


class Node:
    """Minimal accessible object; extents are in the node's own coordinate space."""

    def __init__(self, role, name="", extents=(0, 0, 0, 0), states=("showing",), children=()):
        self.role, self.name, self.extents, self.states = role, name, Extents(*extents), states
        self.children, self.parent = list(children), None
        for child in self.children:
            child.parent = self

    def get_role_name(self):
        return self.role

    def get_name(self):
        return self.name

    def get_state_set(self):
        return States(self.states)

    def get_extents(self, _coord_type):
        return self.extents

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, index):
        return self.children[index]

    def get_parent(self):
        return self.parent

    def get_accessible_at_point(self, x, y, _coord_type):
        for child in reversed(self.children):
            e = child.extents
            if e.x <= x < e.x + e.width and e.y <= y < e.y + e.height:
                return child
        return None


class ToolkitNode(Node):
    """Chromium's views layer: get_accessible_at_point answers with an unrelated object."""

    def get_accessible_at_point(self, x, y, _coord_type):
        return self.children[0] if self.children else None


def chromium_window(title="Library - Brave", states=("showing", "active"), size=(1256, 750)):
    """Browser UI in logical pixels; the web document and its content in physical (2x) pixels."""
    link = Node("link", "Library", (24, 480, 466, 72))
    label = Node("static", "Library", (40, 490, 100, 50))
    link.children, label.parent = [label], link
    top_link = Node("link", "Top", (0, 170, 400, 40))
    document = Node("document web", "Library", (0, 160, 2512, 1340), children=[top_link, link])
    favicon = Node("panel", "", (12, 9, 22, 22))
    tab = Node("page tab", "Library", (0, 0, 142, 40), children=[favicon])
    back = Node("push button", "Back", (0, 40, 40, 40))
    grouping = Node("panel", "", (0, 0, 0, 0), children=[back])
    toolbar = ToolkitNode("tool bar", "", (0, 0, 1256, 80), children=[tab, grouping])
    panel = ToolkitNode("panel", "", (0, 80, 1256, 670), children=[document])
    empty_overlay = ToolkitNode("panel", "", (0, 0, *size))
    hidden_overlay = ToolkitNode("panel", "", (0, 0, *size), states=())
    return ToolkitNode("frame", title, (0, 0, *size), states,
                       children=[toolbar, panel, empty_overlay, hidden_overlay])


class HitTestTest(unittest.TestCase):
    def test_web_content_point_is_scaled_inside_the_document(self):
        frame = chromium_window()

        node = coach.hit_test(frame, 100, 250, 2.0, FakeAtspi)
        target, document = coach.actionable_target(node)

        self.assertEqual((target.role, target.name), ("link", "Library"))
        self.assertEqual(document.role, "document web")

    def test_unscaled_lookup_would_miss_the_link(self):
        frame = chromium_window()

        node = coach.hit_test(frame, 100, 250, 1.0, FakeAtspi)

        self.assertNotEqual(node.role, "link")

    def test_browser_ui_is_descended_by_bounds_not_toolkit_answers(self):
        frame = chromium_window()

        target, document = coach.actionable_target(coach.hit_test(frame, 20, 60, 2.0, FakeAtspi))

        self.assertEqual((target.role, target.name), ("push button", "Back"))
        self.assertIsNone(document)

    def test_link_at_the_top_edge_of_a_document_is_found(self):
        frame = chromium_window()

        target, document = coach.actionable_target(coach.hit_test(frame, 50, 95, 2.0, FakeAtspi))

        self.assertEqual((target.role, target.name), ("link", "Top"))
        self.assertIsNotNone(document)

    def test_higher_dialog_control_beats_a_deeper_control_below_it(self):
        deep_button = Node("push button", "Reload", (0, 0, 50, 50))
        toolbar = Node("tool bar", "", (0, 0, 100, 100), children=[
            Node("panel", "", (0, 0, 100, 100), children=[Node("panel", "", (0, 0, 100, 100), children=[deep_button])]),
        ])
        dialog_button = Node("push button", "Cancel", (0, 0, 60, 60))
        dialog = Node("dialog", "", (0, 0, 100, 100), children=[dialog_button])
        frame = ToolkitNode("frame", "", (0, 0, 100, 100), children=[toolbar, dialog])

        self.assertIs(coach.hit_test(frame, 10, 10, 1.0, FakeAtspi), dialog_button)

    def test_actionable_search_stops_at_the_document_boundary(self):
        text = Node("static", "Hello", (0, 0, 10, 10))
        document = Node("document web", "", (0, 0, 100, 100), children=[text])
        button = Node("push button", "Web view", (0, 0, 100, 100), children=[document])
        Node("frame", "", (0, 0, 100, 100), children=[button])

        target, found_document = coach.actionable_target(text)

        self.assertIs(target, text)
        self.assertIs(found_document, document)


class FrameMatchTest(unittest.TestCase):
    def test_focused_client_matches_the_active_frame_among_same_titles(self):
        inactive = chromium_window(title="Chat", states=("showing",))
        active = chromium_window(title="Chat")
        app = Node("application", "Brave", children=[inactive, active])
        client = {"title": "Chat", "focusHistoryID": 0, "size": [1256, 750]}

        self.assertIs(coach.accessible_frame(app, client, FakeAtspi), active)

    def test_indistinguishable_frames_are_refused(self):
        first = chromium_window(title="Chat", states=("showing",))
        second = chromium_window(title="Chat", states=("showing",))
        app = Node("application", "Brave", children=[first, second])
        client = {"title": "Chat", "focusHistoryID": 3, "size": [1256, 750]}

        self.assertIsNone(coach.accessible_frame(app, client, FakeAtspi))

    def test_unfocused_client_does_not_take_the_active_frame(self):
        active = chromium_window(title="Chat")
        other = chromium_window(title="Chat", states=("showing",), size=(621, 368))
        app = Node("application", "Brave", children=[active, other])
        client = {"title": "Chat", "focusHistoryID": 2, "size": [621, 368]}

        self.assertIs(coach.accessible_frame(app, client, FakeAtspi), other)

    def test_hovered_popup_menu_item_is_found(self):
        item = Node("menu item", "Settings", states=("showing", "selected"))
        menu = Node("menu", "", children=[Node("menu item", "History"), item])
        app = Node("application", "Brave", children=[chromium_window(), menu])

        self.assertIs(coach.popup_menu_item(app, FakeAtspi), item)


def client(title, at, size, focus, workspace=1, **extra):
    return {"title": title, "at": at, "size": size, "focusHistoryID": focus,
            "workspace": {"id": workspace}, "mapped": True, "hidden": False, **extra}


class WindowAtTest(unittest.TestCase):
    monitors = [{"id": 0, "scale": 2, "activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}}]

    def test_focused_window_under_pointer_wins(self):
        clients = [client("maximized", [12, 38], [1256, 750], 1, fullscreen=1), client("tile", [647, 38], [301, 180], 0)]

        self.assertEqual(coach.window_at(700, 60, clients, self.monitors)["title"], "tile")

    def test_floating_window_beats_tiled_when_neither_is_focused(self):
        clients = [client("tile", [0, 0], [800, 800], 2), client("float", [100, 100], [200, 200], 3, floating=True)]

        self.assertEqual(coach.window_at(150, 150, clients, self.monitors)["title"], "float")

    def test_windows_on_hidden_workspaces_are_ignored(self):
        clients = [client("elsewhere", [0, 0], [800, 800], 0, workspace=2)]

        self.assertEqual(coach.window_at(10, 10, clients, self.monitors), {})


class BarWidgetTest(unittest.TestCase):
    def test_panel_position_skips_non_panels(self):
        widgets = [
            {"id": "omarchy.audio", "section": "right", "x": 0, "y": 0, "width": 20, "height": 20, "visible": True},
            {"id": "omarchy.media", "section": "right", "x": 20, "y": 0, "width": 20, "height": 20, "visible": True},
            {"id": "example.settings", "section": "right", "x": 40, "y": 0, "width": 20, "height": 20, "visible": True},
        ]

        context = coach.bar_widget_context(widgets, 45, 10, {"omarchy.audio": "Audio", "example.settings": "Settings"})

        self.assertEqual((context["panel_index"], context["widget_name"]), (2, "Settings"))

    def test_panel_position_uses_shell_layout_order(self):
        widgets = [
            {"id": "example.settings", "section": "right", "x": 20, "y": 0, "width": 20, "height": 20, "visible": True},
            {"id": "omarchy.audio", "section": "right", "x": 0, "y": 0, "width": 20, "height": 20, "visible": True},
        ]

        context = coach.bar_widget_context(
            widgets, 25, 10, {"omarchy.audio": "Audio", "example.settings": "Settings"},
            ["omarchy.audio", "example.settings"],
        )

        self.assertEqual(context["panel_index"], 2)

    def test_widget_missing_from_layout_gets_no_index_instead_of_crashing(self):
        widgets = [{"id": "example.settings", "section": "right", "x": 0, "y": 0, "width": 20, "height": 20, "visible": True}]

        context = coach.bar_widget_context(widgets, 5, 5, {"example.settings": "Settings"}, ["omarchy.audio"])

        self.assertNotIn("panel_index", context)

    def test_unexpected_geometry_payload_is_tolerated(self):
        self.assertEqual(coach.bar_widget_context({"error": "x"}, 5, 5, {}), {"namespace": "omarchy-bar", "surface": "bar"})


class ShortcutFormattingTest(unittest.TestCase):
    def test_gtk_and_aria_bindings_are_normalized(self):
        for raw, expected in (("<Primary>o", "Ctrl+O"), ("<Primary><Shift>n", "Ctrl+Shift+N"),
                              ("Control+Shift+O", "Ctrl+Shift+O"), ("Escape", "Esc"), ("", "")):
            with self.subTest(raw=raw):
                self.assertEqual(coach.normalize_key_binding(raw), expected)

    def test_menu_opening_escape_is_ignored(self):
        target = {"role": "push button", "name": "More options",
                  "actions": [{"name": "click", "description": "", "key_binding": "Escape"}]}

        self.assertIsNone(coach.exposed_shortcut(target))


class TooltipHintTest(unittest.TestCase):
    # The same table as keyboard_coach::harvest::accessibility's tests.
    CASES = {
        "Play (k)": ("Play", "k"),
        "Next (SHIFT+n)": ("Next", "Shift+N"),
        "Toggle sidebar (Ctrl+B)": ("Toggle sidebar", "Ctrl+B"),
        "Zoom in (Ctrl++)": ("Zoom in", "Ctrl++"),
        "Exit full screen (Esc)": ("Exit full screen", "Esc"),
        "Search (/)": ("Search", "/"),
        "Help (F1)": ("Help", "F1"),
        "Comments (3)": None,
        "Settings (Beta)": None,
        "Inbox (1 of 5)": None,
        "Merge (Shift+Something)": None,
        "(k)": None,
        "Next page": None,
    }

    def test_hints_match_the_rust_harvester(self):
        for text, expected in self.CASES.items():
            with self.subTest(text):
                self.assertEqual(coach.tooltip_hint(text), expected)


class ObservedShortcutsTest(unittest.TestCase):
    def test_observed_shortcuts_join_the_installed_app_first(self):
        harvested = {"apps": [{"id": "org.app", "classes": ["app"], "sources": [{"harvester": "gtk"}], "shortcuts": [{"title": "Save", "keys": ["Ctrl+S"]}]}]}
        observed = {"apps": [
            {"id": "app", "classes": ["app"], "sources": [{"harvester": "accessibility"}], "toolkit": "gtk", "shortcuts": [{"title": "Save", "keys": ["Ctrl+Shift+S"]}]},
            {"id": "other", "classes": ["other"], "sources": [], "shortcuts": [{"title": "Open", "keys": ["Ctrl+O"]}]},
        ]}
        coach.merge_observed(harvested, observed)
        self.assertEqual([app["id"] for app in harvested["apps"]], ["org.app", "other"])
        self.assertEqual([s["keys"][0] for s in harvested["apps"][0]["shortcuts"]], ["Ctrl+Shift+S", "Ctrl+S"])
        self.assertEqual([s["harvester"] for s in harvested["apps"][0]["sources"]], ["gtk", "accessibility"])

    def test_capture_runs_once_per_window_and_site_per_interval(self):
        started = []

        class FakeProcess:
            def __init__(self, argv, **_):
                started.append(argv)

            def poll(self):
                return 0

        original = (coach.subprocess.Popen, coach.harvest_command, coach._observer, dict(coach._observed_at))
        coach.subprocess.Popen, coach.harvest_command = FakeProcess, lambda: ["harvest"]
        coach._observer, coach._observed_at = None, {}
        try:
            native = {"app": "linguist", "pid": 7, "omarchy": {}, "target": {"role": "push button", "in_document": False}}
            page = {"app": "brave-browser", "pid": 9, "omarchy": {}, "target": {"role": "link", "in_document": True, "site": "a.com"}}
            other_site = {**page, "target": {**page["target"], "site": "b.com"}}
            terminal = {"app": "foot", "pid": 8, "omarchy": {}, "target": {}}
            self.assertTrue(coach.maybe_observe(native, now=0))
            self.assertFalse(coach.maybe_observe(native, now=60))
            self.assertTrue(coach.maybe_observe(page, now=60))
            self.assertTrue(coach.maybe_observe(other_site, now=61))
            self.assertFalse(coach.maybe_observe(terminal, now=62))
            self.assertTrue(coach.maybe_observe(native, now=coach.OBSERVE_INTERVAL_SECONDS + 1))
            self.assertEqual(started[0], ["harvest", "observe", "--pid", "7", "--class", "linguist"])
            self.assertEqual(len(started), 4)
        finally:
            coach.subprocess.Popen, coach.harvest_command, coach._observer, coach._observed_at = original

    def test_capture_waits_for_a_running_capture(self):
        class Running:
            def poll(self):
                return None

        original = (coach._observer, dict(coach._observed_at))
        coach._observer, coach._observed_at = Running(), {}
        try:
            context = {"app": "linguist", "pid": 7, "omarchy": {}, "target": {"role": "push button"}}
            self.assertFalse(coach.maybe_observe(context, now=0))
        finally:
            coach._observer, coach._observed_at = original


class ClickSequenceTest(unittest.TestCase):
    def test_each_click_updates_the_banner(self):
        import os
        import tempfile

        calls = []
        contexts = iter([
            {"app": "t3code", "target": {"role": "push button", "name": "Play (k)"}},
            {"app": "t3code", "target": {"role": "push button", "name": "Sidebar (Ctrl+B)"}},
            {"app": "t3code", "target": {"role": "section", "name": ""}},
        ])
        saved = {name: getattr(coach, name) for name in ("run", "press_context", "maybe_observe", "record_unmatched")}
        saved_env = {key: os.environ.get(key) for key in ("XDG_RUNTIME_DIR", "XDG_STATE_HOME")}
        with tempfile.TemporaryDirectory() as directory:
            os.environ["XDG_RUNTIME_DIR"] = os.environ["XDG_STATE_HOME"] = directory
            coach.run = lambda argv, **_: calls.append(argv[3:5] if argv[3] == "show" else argv[3:4])
            coach.press_context = lambda button: next(contexts)
            coach.maybe_observe = lambda context: False
            coach.record_unmatched = lambda context: None
            try:
                for _ in range(3):
                    coach.handle_click("left", catalog={"entries": [], "command_sets": []})
            finally:
                for name, value in saved.items():
                    setattr(coach, name, value)
                for key, value in saved_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                coach._banner_visible_until = 0.0
        import json

        rendered = [[call[0], json.loads(call[1])["message"]] if call[0] == "show" else call for call in calls]
        self.assertEqual(rendered, [["show", "k — Play."], ["show", "Ctrl+B — Sidebar."], ["close"]])


class FakeHerdrServer:
    """A herdr API socket that serves snapshots and pushes an event on demand."""

    def __init__(self, path):
        self.snapshot = {"focused_workspace_id": "w1", "focused_tab_id": "w1:t1", "focused_pane_id": "w1:p1", "tabs": [], "workspaces": [], "layouts": []}
        self.subscribers = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        stream = connection.makefile("rwb")
        request = json.loads(stream.readline())
        if request["method"] == "session.snapshot":
            reply = {"type": "session_snapshot", "snapshot": self.snapshot}
        else:
            reply = {"type": "subscription_started"}
            self.subscribers.append(stream)
        stream.write(json.dumps({"id": request["id"], "result": reply}).encode() + b"\n")
        stream.flush()
        if request["method"] == "session.snapshot":
            connection.close()

    def focus_tab(self, tab):
        self.snapshot = {**self.snapshot, "focused_tab_id": tab}
        for stream in self.subscribers:
            stream.write(b'{"event":"tab_focused","data":{}}\n')
            stream.flush()

    def close(self):
        self.server.close()


class HerdrTest(unittest.TestCase):
    def wait_for(self, condition):
        deadline = time.monotonic() + 2
        while not condition():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)

    def test_watcher_reports_the_state_before_a_click_changed_it(self):
        with tempfile.TemporaryDirectory() as directory:
            server = FakeHerdrServer(f"{directory}/herdr.sock")
            try:
                watcher = coach.HerdrWatcher(f"{directory}/herdr.sock")
                self.wait_for(lambda: watcher.state is not None and server.subscribers)
                self.assertIsNone(watcher.effect(0))

                pressed_at = time.monotonic()
                server.focus_tab("w1:t2")
                self.wait_for(lambda: watcher.state["tab"] == "w1:t2")

                effect = watcher.effect(pressed_at)
                self.assertEqual((effect["before"]["tab"], effect["after"]["tab"]), ("w1:t1", "w1:t2"))
                self.assertIsNone(watcher.effect(time.monotonic()))
            finally:
                server.close()

    def test_client_session_from_the_terminal_process_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)

            def process(pid, argv, children=()):
                (proc / str(pid) / "task" / str(pid)).mkdir(parents=True)
                (proc / str(pid) / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")
                (proc / str(pid) / "task" / str(pid) / "children").write_text(" ".join(map(str, children)))

            process(10, ["foot"], [11])
            process(11, ["/bin/bash"], [12])
            process(12, ["herdr", "--session", "work"])
            process(20, ["foot"], [21])
            process(21, ["herdr", "--remote", "pi"])
            process(30, ["foot"], [31])
            process(31, ["/usr/bin/herdr"])

            real_path = coach.Path
            coach.Path = lambda value: real_path(str(value).replace("/proc", directory, 1))
            try:
                self.assertEqual(coach.herdr_client_session(10), "work")
                self.assertIsNone(coach.herdr_client_session(20))
                self.assertEqual(coach.herdr_client_session(30), "default")
            finally:
                coach.Path = real_path


class CoachingPolicyTest(unittest.TestCase):
    def config(self, **overrides):
        import copy
        return {**copy.deepcopy(coach.CONFIG_DEFAULTS), **overrides}

    def decide(self, config, history=(), suggestion="Ctrl+T — open a new tab.", app="brave-browser"):
        return coach.suggestion_decision(suggestion, {"app": app}, config, list(history), now=1000000.0)

    def history(self, count, suggestion="Ctrl+T — open a new tab.", shown=True):
        return [{"time": 999000.0, "suggestion": suggestion, "shown": shown} for _ in range(count)]

    def test_the_shipped_settings_suggest_on_the_first_click(self):
        self.assertEqual(self.decide(self.config()), (True, ""))

    def test_a_repeat_threshold_waits_for_the_habit(self):
        config = self.config(repeats_before_suggesting=3)
        self.assertEqual(self.decide(config)[0], False)
        self.assertEqual(self.decide(config, self.history(1))[0], False)
        self.assertEqual(self.decide(config, self.history(2)), (True, ""))

    def test_repeats_outside_the_window_do_not_count(self):
        config = self.config(repeats_before_suggesting=2, repeat_window_hours=1)
        old = [{"time": 1000.0, "suggestion": "Ctrl+T — open a new tab.", "shown": True}]
        self.assertEqual(self.decide(config, old)[0], False)

    def test_a_muted_app_stays_silent(self):
        shown, reason = self.decide(self.config(mute_apps=["^brave"]))
        self.assertEqual((shown, reason), (False, "muted app"))
        self.assertTrue(self.decide(self.config(mute_apps=["^signal$"]))[0])

    def test_a_learned_shortcut_can_be_muted(self):
        self.assertEqual(self.decide(self.config(mute_suggestions=[r"^Ctrl\+T\b"]))[0], False)

    def test_coaching_can_stop_after_enough_reminders(self):
        config = self.config(stop_after_suggestions=2)
        self.assertTrue(self.decide(config, self.history(1))[0])
        self.assertFalse(self.decide(config, self.history(2))[0])
        # Only the times it was actually shown count towards the limit.
        self.assertTrue(self.decide(config, self.history(5, shown=False))[0])

    def test_quiet_hours_cover_the_night(self):
        overnight = self.config(quiet_hours={"from": "22:00", "to": "07:00"})
        daytime = self.config(quiet_hours={"from": "09:00", "to": "17:00"})
        at = lambda hour, minute=0: time.struct_time((2026, 1, 1, hour, minute, 0, 3, 1, 0))
        self.assertTrue(coach.in_quiet_hours(overnight, at(23)))
        self.assertTrue(coach.in_quiet_hours(overnight, at(3)))
        self.assertFalse(coach.in_quiet_hours(overnight, at(12)))
        self.assertTrue(coach.in_quiet_hours(daytime, at(9)))
        self.assertFalse(coach.in_quiet_hours(daytime, at(17)))
        self.assertFalse(coach.in_quiet_hours(self.config(), at(3)))

    def test_configuration_is_read_leniently(self):
        import os
        saved = os.environ.get("XDG_CONFIG_HOME")
        with tempfile.TemporaryDirectory() as directory:
            os.environ["XDG_CONFIG_HOME"] = directory
            try:
                self.assertEqual(coach.load_config(), coach.CONFIG_DEFAULTS)
                path = Path(directory) / "keyboard-coach/config.json"
                path.parent.mkdir(parents=True)
                path.write_text('{"banner_duration_ms": 4000, "mute_apps": ["^x$"], "repeats_before_suggesting": "3"}')
                config = coach.load_config()
                self.assertEqual(config["banner_duration_ms"], 4000)
                self.assertEqual(config["mute_apps"], ["^x$"])
                # A value of the wrong type falls back rather than failing.
                self.assertEqual(config["repeats_before_suggesting"], 1)
                path.write_text("{ not json")
                self.assertEqual(coach.load_config(), coach.CONFIG_DEFAULTS)
            finally:
                if saved is None:
                    del os.environ["XDG_CONFIG_HOME"]
                else:
                    os.environ["XDG_CONFIG_HOME"] = saved


@contextlib.contextmanager
def isolated_home():
    """Run against empty config, state and runtime directories.

    The coach reads the user's real ones, and a test must never depend on, or
    write to, what is actually on this machine.
    """
    names = ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR")
    saved = {name: os.environ.get(name) for name in names}
    with tempfile.TemporaryDirectory() as directory:
        for name in names:
            os.environ[name] = str(Path(directory) / name.lower())
            Path(os.environ[name]).mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            yield Path(directory)
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


class ReportTest(unittest.TestCase):
    def test_report_ranks_habits_and_compares_with_the_period_before(self):
        now = 2_000_000.0
        with isolated_home():
            records = (
                [{"time": now - 3600, "app": "brave-browser", "suggestion": "Ctrl+T — open a new tab.", "shown": True}] * 3
                + [{"time": now - 7200, "app": "nautilus", "suggestion": "F2 — rename.", "shown": False}]
                + [{"time": now - 10 * 86400, "app": "brave-browser", "suggestion": "Ctrl+T — open a new tab.", "shown": True}] * 5
            )
            coach.history_path().write_text("\n".join(json.dumps(record) for record in records) + "\n")
            summary = coach.report_summary(days=7, now=now)
            self.assertEqual(summary["coached_clicks"], 4)
            self.assertEqual(summary["previous_period_clicks"], 5)
            self.assertEqual(summary["distinct_actions"], 2)
            self.assertEqual(summary["top"][0]["suggestion"], "Ctrl+T — open a new tab.")
            self.assertEqual(summary["top"][0]["clicks"], 3)
            self.assertEqual(summary["top"][0]["apps"], ["brave-browser"])


class PrivacyTest(unittest.TestCase):
    """The coach can see everything on screen, so what it keeps is the promise."""

    def write_config(self, **settings):
        path = coach.config_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps(settings), encoding="utf-8")

    def test_an_ignored_app_is_never_looked_at_or_captured(self):
        with isolated_home():
            self.write_config(ignore_apps=["^org.keepassxc", "banking"])
            self.assertTrue(coach.ignored_app("org.keepassxc.KeePassXC"))
            self.assertTrue(coach.ignored_app("my-banking-app"))
            self.assertFalse(coach.ignored_app("brave-browser"))
            # No capture is started for it, from a click or from opening it.
            self.assertFalse(coach.observe_window(4242, "org.keepassxc.KeePassXC"))
            coach.queue_observation("org.keepassxc.KeePassXC")
            self.assertNotIn("org.keepassxc.KeePassXC", coach._pending_observations)

    def test_history_can_be_turned_off(self):
        with isolated_home():
            self.write_config(history=False)
            self.assertFalse(coach.load_config()["history"])
            summary = coach.report_summary(days=7)
            self.assertIs(summary["history"], False)
            self.assertEqual(summary["coached_clicks"], 0)

    def test_history_only_accepts_a_real_boolean(self):
        with isolated_home():
            self.write_config(history="no", repeats_before_suggesting=True)
            config = coach.load_config()
            # A string is not a switch, and True is not the number 1.
            self.assertIs(config["history"], True)
            self.assertEqual(config["repeats_before_suggesting"], 1)

    def test_what_is_recorded_is_readable_only_by_its_owner(self):
        with isolated_home():
            coach.record_suggestion({"time": 1, "app": "brave-browser", "suggestion": "Ctrl+T", "shown": True})
            coach.record_unmatched({"app": "brave-browser", "target": {"role": "button"}})
            for path in (coach.history_path(), coach.unmatched_path()):
                self.assertEqual(path.stat().st_mode & 0o077, 0, path)

    def test_the_unmatched_log_keeps_no_labels_or_titles(self):
        with isolated_home():
            coach.record_unmatched({
                "app": "brave-browser", "title": "Payslip March 2026 — Brave",
                "target": {"role": "push button", "name": "Download payslip.pdf",
                           "description": "Save to Documents", "in_document": True,
                           "site": "payroll.example.com"},
            })
            record = json.loads(coach.unmatched_path().read_text(encoding="utf-8"))
            self.assertEqual(record["app"], "brave-browser")
            self.assertEqual(record["site"], "payroll.example.com")
            self.assertNotIn("payslip", json.dumps(record).lower())
            self.assertNotIn("title", record)

    def test_forget_removes_everything_recorded(self):
        with isolated_home():
            coach.record_suggestion({"time": 1, "app": "brave-browser", "suggestion": "Ctrl+T", "shown": True})
            coach.record_unmatched({"app": "brave-browser", "target": {}})
            coach.observed_path().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            coach.observed_path().write_text("{}", encoding="utf-8")
            self.assertTrue(any(path.exists() for path in coach.recorded_paths()))

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(coach.forget(), 0)

            self.assertEqual([path for path in coach.recorded_paths() if path.exists()], [])
            # Settings survive being forgotten; they are the user's, not a record.
            self.write_config(history=False)
            with contextlib.redirect_stdout(io.StringIO()):
                coach.forget()
            self.assertTrue(coach.config_path().exists())

    def test_nothing_reaches_the_network(self):
        """No suggestion is worth a request, so nothing can make one."""
        sources = [ROOT / "scripts/coach.py", ROOT / "scripts/ingest_catalog.py"]
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for forbidden in ("urlopen", "http.client", "requests.", "AF_INET", "socket.create_connection"):
                self.assertNotIn(forbidden, text, f"{path.name} reaches the network")
        manifest = (ROOT / "Cargo.toml").read_text(encoding="utf-8")
        for crate in ("reqwest", "hyper", "ureq", "curl", "tokio-tungstenite"):
            self.assertNotIn(f"\n{crate}", manifest, f"{crate} is a network client")


class CoverageTest(unittest.TestCase):
    def test_coverage_counts_indexed_sources(self):
        catalog = {
            "version": 3, "command_sets": [{"id": "example"}], "entries": [{"shortcut": "X"}],
            "system_index": {
                "desktop_apps": [{}, {}], "hypr_bindings": [{"command": "a"}, {"command": ""}],
                "hypr_binding_source": "lua-replay",
                "plugins": [{"bar_panel": True, "declared_shortcuts": [{}]}, {"bar_panel": False}],
            },
        }

        report = coach.coverage_report(catalog)

        self.assertEqual(report["installed_apps"], 2)
        self.assertEqual(report["bindings_with_commands"], 1)
        self.assertEqual(report["bar_panel_plugins"], 1)


if __name__ == "__main__":
    unittest.main()
