
#!/usr/bin/env python3
# Warehouse Clerk — Textual port with live theme switching
# Keys:
#   u = Upload (zenity or prompt)      s = Search tab + focus
#   o = Open found PDF                 q = Quit
#   t = Cycle theme                    T = Choose theme by name

import asyncio
import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, List

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive, var
from textual.widgets import (
    Header,
    Footer,
    Static,
    Input,
    Button,
    DataTable,
    LoadingIndicator,
    TabPane,
    TabbedContent,
    Label,
)

try:
    from pyfiglet import Figlet
except Exception:
    Figlet = None

APP_TITLE = "PDF PARSER TERMINAL UI"
DB_PATH = "warehouse.db"
PARSER = [sys.executable, "parse_cli.py"]  # uses your current Python to run parse_cli.py


# ----------------------------- Themes -----------------------------

@dataclass
class Theme:
    name: str
    background: str
    foreground: str
    accent: str
    surface: str
    accent_2: str  # used where cyan was used before

THEMES: List[Theme] = [
    Theme("dark",   "#0b1020", "#e5e7eb", "#a78bfa", "#11162a", "#22d3ee"),  # original
    Theme("bright", "#f7f7f7", "#111111", "#3b82f6", "#e5e7eb", "#0ea5e9"),
    Theme("tokyo",  "#1a1b26", "#c0caf5", "#7aa2f7", "#24283b", "#2ac3de"),  # Tokyo Night
    Theme("barbie", "#ffe4f1", "#5c1a3a", "#ff4fae", "#ffd1e6", "#ff86d0"),  # pink vibes
    Theme("matrix", "#000000", "#00ff9c", "#00d084", "#001a12", "#00f0c8"),  # black/green
    Theme("nord",   "#2e3440", "#e5e9f0", "#88c0d0", "#3b4252", "#a3be8c"),
    Theme("gruvbox","#282828", "#ebdbb2", "#fabd2f", "#3c3836", "#83a598"),
    Theme("dracula","#282a36", "#f8f8f2", "#bd93f9", "#1e1f29", "#8be9fd"),
    Theme("solarlt","#fdf6e3", "#073642", "#268bd2", "#eee8d5", "#2aa198"),
]

def has_zenity() -> bool:
    return shutil.which("zenity") is not None


async def run_subprocess(*args: str) -> Tuple[int, str, str]:
    """Run a subprocess and return (rc, stdout, stderr) as strings."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def run_parse_cli(file_path: str) -> Dict:
    """Run parse_cli.py <file> and return parsed JSON as dict; raise on error."""
    rc, out, err = await run_subprocess(*PARSER, file_path)
    if rc != 0:
        raise RuntimeError(f"Python error (rc={rc}):\n{err or out}")
    try:
        return json.loads(out)
    except Exception as e:
        raise RuntimeError(f"JSON parse error: {e}\nOutput:\n{out}") from e


def search_db(po_number: str) -> Optional[str]:
    """Return pdf_path for PO or None if not found. Raises on DB errors."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT pdf_path FROM purchase_orders WHERE po_number = ?",
            (po_number.strip(),),
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


async def xdg_open(path: str) -> None:
    # Fire-and-forget; don't await completion
    asyncio.create_task(run_subprocess("xdg-open", path))


# ----------------------------- Widgets -----------------------------

class HelpBar(Static):
    """Minimal help footer that mirrors your Go key map."""
    def compose(self) -> ComposeResult:
        yield Label("u Upload • s Search • o Open PDF • t/T Theme • q Quit", id="helpbar")


class StatusBar(Static):
    text = reactive("Ready.")

    def watch_text(self, text: str) -> None:
        self.update(text)


@dataclass
class ParseState:
    kv: Dict[str, str]
    raw: str


class UploadPane(Vertical):
    """Upload tab: shows table of parsed key/values and raw JSON output."""

    DEFAULT_CSS = """
    UploadPane {
        layout: vertical;
        padding: 0 0;
    }
    #upload_actions {
        height: auto;
        content-align: center middle;
        margin: 0 0 1 0;
    }
    #upload_spinner {
        height: 3;
        content-align: center middle;
        margin: 0 0 1 0;
    }
    #kv_table {
        border: round white; /* color overridden programmatically */
        padding: 0 0;
        margin: 0 0 1 0;
    }
    #raw_wrap {
        height: 12;
        border: round white; /* color overridden programmatically */
    }
    #raw_log {
        padding: 0 1;
        content-align: left top;
        height: auto;
    }
    """

    parsing = reactive(False)
    state: Optional[ParseState] = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="upload_actions"):
            yield Button("Pick PDF (u)", id="pick_btn", variant="primary")
            yield Button("Clear", id="clear_btn", variant="default")

        yield Static("", id="upload_spinner")

        table = DataTable(id="kv_table")
        table.add_column("Field")
        table.add_column("Value", width=80)
        yield table

        with VerticalScroll(id="raw_wrap"):
            yield Static("Raw JSON will appear here after parsing.", id="raw_log")

    async def show_spinner(self, show: bool, message: str = "Parsing…") -> None:
        self.parsing = show
        spot = self.query_one("#upload_spinner", Static)
        if show:
            spot.update(LoadingIndicator() + Static(f" {message}"))
        else:
            spot.update("")

    def set_state(self, payload: ParseState) -> None:
        self.state = payload
        table = self.query_one(DataTable)
        table.clear()
        for k in sorted(payload.kv.keys(), key=lambda s: s.lower()):
            v = str(payload.kv[k])
            table.add_row(k, v)
        raw = self.query_one("#raw_log", Static)
        raw.update(payload.raw)

    def clear(self) -> None:
        self.state = None
        self.query_one(DataTable).clear()
        raw = self.query_one("#raw_log", Static)
        raw.update("Raw JSON will appear here after parsing.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pick_btn":
            self.post_message_no_wait(UploadPickRequested())
        elif event.button.id == "clear_btn":
            self.clear()


class SearchPane(Vertical):
    """Search tab: PO input, result label, open button."""

    DEFAULT_CSS = """
    SearchPane {
        layout: vertical;
    }
    #search_bar {
        layout: horizontal;
        height: auto;
        content-align: center middle;
        margin: 0 0 1 0;
    }
    #result_box {
        border: round white; /* color overridden programmatically */
        height: 6;
        padding: 1 1;
        margin: 0 0 1 0;
    }
    """

    pdf_path: var[Optional[str]] = var(None)

    def compose(self) -> ComposeResult:
        with Horizontal(id="search_bar"):
            yield Static("PO:", classes="label")
            yield Input(placeholder="Type PO number and press Enter…", id="po_input")
            yield Button("Search", id="search_btn", variant="primary")
        yield Static("—", id="result_box")
        with Horizontal():
            yield Button("Open PDF (o)", id="open_btn", disabled=True)

    def focus_input(self) -> None:
        self.query_one("#po_input", Input).focus()

    def set_result(self, text: str, pdf: Optional[str]) -> None:
        self.pdf_path = pdf
        result = self.query_one("#result_box", Static)
        result.update(text)
        open_btn = self.query_one("#open_btn", Button)
        open_btn.disabled = not bool(pdf)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "search_btn":
            po = self.query_one("#po_input", Input).value.strip()
            self.post_message_no_wait(SearchRequested(po))
        elif event.button.id == "open_btn":
            if self.pdf_path:
                self.post_message_no_wait(OpenRequested(self.pdf_path))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "po_input":
            po = event.value.strip()
            self.post_message_no_wait(SearchRequested(po))


# ----------------------------- Messages -----------------------------

from textual.message import Message

class UploadPickRequested(Message):
    pass

class UploadPicked(Message):
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        super().__init__()

class SearchRequested(Message):
    def __init__(self, po: str) -> None:
        self.po = po
        super().__init__()

class OpenRequested(Message):
    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__()


# ----------------------------- App -----------------------------

class WarehouseClerkApp(App):
    # Keep CSS for layout; all colors applied programmatically for theme switching
    CSS = """
    Screen {
        /* colors set programmatically */
    }

    #titlebar {
        width: 1fr;
        height: 3;
        content-align: left middle;
        padding: 0 2;
        border: none;
        /* colors set programmatically */
    }

    #banner {
        padding: 1 2;
        content-align: center middle;
        /* color set programmatically */
    }

    #helpbar {
        padding: 0 2;
        height: 1;
        content-align: center middle;
        /* color set programmatically */
    }

    #statusbar {
        padding: 0 1;
        height: 3;
        content-align: center middle;
        border: round white; /* color set programmatically */
    }

    TabbedContent {
        border: round white; /* color set programmatically */
        margin: 1 2;
    }

    Tab.-active {
        /* color set programmatically */
    }

    .label {
        width: 6;
        content-align: right middle;
        /* color set programmatically */
    }

    Button {
        border: round white; /* color set programmatically */
    }
    Button.-primary {
        border: round white; /* color set programmatically */
    }
    """

    BINDINGS = [
        Binding("u", "upload", "Upload PDF"),
        Binding("s", "focus_search", "Search PO"),
        Binding("o", "open_found", "Open Found PDF"),
        Binding("t", "cycle_theme", "Cycle Theme"),
        Binding("T", "choose_theme", "Choose Theme"),
        Binding("q", "quit", "Quit"),
    ]

    status = reactive("Ready.")
    found_pdf: Optional[str] = reactive(None)
    theme_index: int = reactive(0)  # start at 0 ("dark")

    def _banner_text(self) -> str:
        text = "WAREHOUSE CLERK"
        if Figlet:
            try:
                return Figlet(font="doom").renderText(text)
            except Exception:
                pass
        return text

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(f"  {APP_TITLE}  ", id="titlebar")
        yield Static(self._banner_text(), id="banner")
        with TabbedContent():
            with TabPane("UPLOAD", id="upload_tab"):
                yield UploadPane(id="upload_pane")
            with TabPane("SEARCH", id="search_tab"):
                yield SearchPane(id="search_pane")
        yield StatusBar(id="statusbar")
        yield HelpBar()
        yield Footer()

    # ---------- Lifecycle ----------

    def on_mount(self) -> None:
        self.apply_theme(THEMES[self.theme_index])

    # ---------- Actions (keybindings) ----------

    def action_quit(self) -> None:
        self.exit()

    async def action_upload(self) -> None:
        """Open zenity (if available) or prompt for a path; then parse."""
        file_path = None
        if has_zenity():
            self.status = "Opening file picker…"
            rc, out, _ = await run_subprocess(
                "zenity",
                "--file-selection",
                "--title=Select a PDF",
                "--file-filter=PDF files | *.pdf",
            )
            if rc == 0:
                file_path = out.strip()
        if not file_path:
            ipt = await self.request_input(
                "Enter PDF path (zenity not available or canceled):"
            )
            file_path = (ipt or "").strip()
        if not file_path:
            self.status = "No file selected."
            return
        self.post_message(UploadPicked(file_path))

    def action_focus_search(self) -> None:
        """Switch to Search tab and focus the input."""
        self.query_one(TabbedContent).active = "search_tab"
        pane = self.query_one("#search_pane", SearchPane)
        pane.focus_input()
        self.status = "Search active. Type PO and press Enter."

    async def action_open_found(self) -> None:
        """Open the last-found PDF (if any)."""
        if self.found_pdf:
            self.status = "Opening PDF…"
            await xdg_open(self.found_pdf)
        else:
            self.status = "No PDF selected/found."

    def action_cycle_theme(self) -> None:
        self.theme_index = (self.theme_index + 1) % len(THEMES)
        self.apply_theme(THEMES[self.theme_index])

    async def action_choose_theme(self) -> None:
        name = await self.request_input(
            f"Theme name ({', '.join(t.name for t in THEMES)}):"
        )
        if not name:
            return
        name = name.strip().lower()
        for i, t in enumerate(THEMES):
            if t.name == name:
                self.theme_index = i
                self.apply_theme(t)
                return
        self.status = f"Unknown theme '{name}'."

    # ---------- Utility ----------

    async def request_input(self, prompt: str) -> Optional[str]:
        """Simple inline input prompt in the status bar; returns value or None."""
        sb = self.query_one(StatusBar)
        inp = Input(placeholder=prompt)
        container = Horizontal(inp, Button("OK", id="ok_btn"))
        await sb.mount(container)

        fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()

        def cleanup() -> None:
            try:
                container.remove()
            except Exception:
                pass

        def on_submit(value: str) -> None:
            if not fut.done():
                fut.set_result(value)

        @inp.on(Input.Submitted)
        def _submitted(ev: Input.Submitted) -> None:
            on_submit(ev.value)

        @container.query_one(Button).on(Button.Pressed)  # type: ignore
        def _ok_pressed(_: Button.Pressed) -> None:
            on_submit(inp.value)

        inp.focus()
        try:
            return await fut
        finally:
            cleanup()

    def apply_theme(self, t: Theme) -> None:
        """Apply colors to all key widgets (no CSS variables; runtime styling)."""
        # Screen
        self.screen.styles.background = t.background
        self.screen.styles.color = t.foreground

        # Titlebar + banner + help/status text
        title = self.query_one("#titlebar", Static)
        title.styles.background = t.background
        title.styles.color = t.accent

        banner = self.query_one("#banner", Static)
        banner.styles.background = t.background
        banner.styles.color = t.accent

        helpbar_label = self.query_one("#helpbar", Label)
        helpbar_label.styles.color = t.foreground

        statusbar = self.query_one("#statusbar", StatusBar)
        statusbar.styles.color = t.foreground
        statusbar.styles.background = t.background
        statusbar.styles.border_top = ("round", t.surface)
        statusbar.styles.border_right = ("round", t.surface)
        statusbar.styles.border_bottom = ("round", t.surface)
        statusbar.styles.border_left = ("round", t.surface)

        # Tabs container border + active tab color
        tabs = self.query_one(TabbedContent)
        tabs.styles.border_top = ("round", t.accent)
        tabs.styles.border_right = ("round", t.accent)
        tabs.styles.border_bottom = ("round", t.accent)
        tabs.styles.border_left = ("round", t.accent)
        tabs.styles.background = t.background
        tabs.styles.color = t.foreground

        # Labels accent
        for lab in self.query(".label"):
            lab.styles.color = t.accent

        # Buttons border color
        for btn in self.query(Button):
            btn.styles.border_top = ("round", t.surface)
            btn.styles.border_right = ("round", t.surface)
            btn.styles.border_bottom = ("round", t.surface)
            btn.styles.border_left = ("round", t.surface)
            if "-primary" in (btn.classes or set()):
                btn.styles.border_top = ("round", t.accent)
                btn.styles.border_right = ("round", t.accent)
                btn.styles.border_bottom = ("round", t.accent)
                btn.styles.border_left = ("round", t.accent)
            btn.styles.color = t.foreground
            btn.styles.background = t.background

        # Upload pane borders + table color
        up = self.query_one("#upload_pane", UploadPane)
        kv_table = up.query_one("#kv_table", DataTable)
        for side in ("border_top", "border_right", "border_bottom", "border_left"):
            setattr(kv_table.styles, side, ("round", t.accent))
        raw_wrap = up.query_one("#raw_wrap", VerticalScroll)
        for side in ("border_top", "border_right", "border_bottom", "border_left"):
            setattr(raw_wrap.styles, side, ("round", t.surface))
        up.query_one("#raw_log", Static).styles.color = t.foreground
        up.query_one("#raw_log", Static).styles.background = t.background

        # Search pane border
        sp = self.query_one("#search_pane", SearchPane)
        result_box = sp.query_one("#result_box", Static)
        for side in ("border_top", "border_right", "border_bottom", "border_left"):
            setattr(result_box.styles, side, ("round", t.surface))
        # Inputs inherit foreground/background
        for wid in (sp.query_one("#po_input", Input),):
            wid.styles.color = t.foreground
            wid.styles.background = t.background

        # Ensure whole screen redraw picks up new palette
        self.refresh()

    # ---------- Message handlers ----------

    async def on_upload_pick_requested(self, _: "UploadPickRequested") -> None:
        await self.action_upload()

    async def on_upload_picked(self, msg: "UploadPicked") -> None:
        pane = self.query_one("#upload_pane", UploadPane)
        await pane.show_spinner(True, f"Parsing: {Path(msg.file_path).name}")
        self.status = "Parsing file…"
        try:
            parsed = await run_parse_cli(msg.file_path)
            # Build kv + pretty raw JSON
            kv = {k: parsed[k] for k in parsed.keys()}
            pretty = json.dumps(parsed, indent=2)
            pane.set_state(ParseState(kv=kv, raw=pretty))
            self.status = "Parsing complete."
            # Switch to Upload tab to show results (if not already there)
            self.query_one(TabbedContent).active = "upload_tab"
        except Exception as e:
            pane.clear()
            self.query_one("#raw_log", Static).update(str(e))
            self.status = "Error parsing file."
        finally:
            await pane.show_spinner(False)

    async def on_search_requested(self, msg: "SearchRequested") -> None:
        pane = self.query_one("#search_pane", SearchPane)
        po = msg.po.strip()
        if not po:
            self.status = "Enter a PO number first."
            return

        self.status = "Searching database…"

        def _work() -> Optional[str]:
            return search_db(po)

        pdf = await asyncio.get_event_loop().run_in_executor(None, _work)
        if pdf is None:
            pane.set_result("PO not found.", None)
            self.found_pdf = None
            self.status = "Search complete."
        else:
            pane.set_result(f"PDF found:\n{pdf}", pdf)
            self.found_pdf = pdf
            self.status = "Search complete. Press 'o' to open the PDF."

    async def on_open_requested(self, msg: "OpenRequested") -> None:
        self.found_pdf = msg.path
        await self.action_open_found()


if __name__ == "__main__":
    app = WarehouseClerkApp()
    app.run()
