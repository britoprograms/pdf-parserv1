
#!/usr/bin/env python3
# Warehouse Clerk — Textual port (compatible CSS + no TextLog)

import asyncio
import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

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


class HelpBar(Static):
    """Minimal help footer that mirrors your Go key map."""
    def compose(self) -> ComposeResult:
        yield Label("u Upload • s Search • o Open PDF • q Quit", id="helpbar")


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
        /* gap: 1; (removed for compatibility) */
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
        /* height: 1fr;  (keep if supported; otherwise comment out) */
        border: round $accent;
        padding: 0 0;
        margin: 0 0 1 0;
    }
    #raw_wrap {
        height: 12;
        border: round $surface;
    }
    #raw_log {
        padding: 0 1;
        content-align: left top;
        height: auto;
        /* no special text-style used for compatibility */
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
        /* gap: 1; (removed for compatibility) */
    }
    #search_bar {
        layout: horizontal;
        height: auto;
        content-align: center middle;
        margin: 0 0 1 0;
    }
    #result_box {
        border: round $surface;
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


# Custom messages
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


class WarehouseClerkApp(App):
    CSS = """
    $background: #0b1020;
    $foreground: #e5e7eb;
    $accent: #a78bfa;
    $surface: #11162a;
    $cyan: #22d3ee;

    Screen {
        background: $background;
        color: $foreground;
    }

    #titlebar {
        width: 1fr;
        height: 3;
        content-align: left middle;
        background: $background;
        color: $accent;
        padding: 0 2;
        border: none;
    }

    #banner {
        color: $accent;
        padding: 1 2;
        content-align: center middle;
    }

    #helpbar {
        color: $foreground 80%;
        padding: 0 2;
        height: 1;
        content-align: center middle;
    }

    #statusbar {
        border: round $surface;
        padding: 0 1;
        height: 3;
        content-align: center middle;
        color: $foreground;
    }

    TabbedContent {
        border: round $accent;
        margin: 1 2;
    }

    /* Avoid 'text-style' for compatibility */
    Tab.-active {
        color: $accent;
    }

    .label {
        width: 6;
        color: $accent;
        content-align: right middle;
    }

    /* Use 'round' instead of 'tall' borders for compatibility */
    Button {
        border: round $surface;
    }
    Button.-primary {
        border: round $accent;
    }
    """

    BINDINGS = [
        Binding("u", "upload", "Upload PDF"),
        Binding("s", "focus_search", "Search PO"),
        Binding("o", "open_found", "Open Found PDF"),
        Binding("q", "quit", "Quit"),
    ]

    status = reactive("Ready.")
    found_pdf: Optional[str] = reactive(None)

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
