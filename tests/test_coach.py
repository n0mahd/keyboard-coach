#!/usr/bin/env python3

import importlib.util
import json
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
