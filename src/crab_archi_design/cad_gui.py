"""Crab-style local dashboard for CrabCADParser.

The parser remains dependency-light and native.  The visual language borrows
the Crab command-deck palette (ink background, coral/orange accents, compact
status surfaces) while keeping the workflow explicit: choose source set,
review the pipeline, run locally, and inspect the generated pack.
"""

from __future__ import annotations

import json
import platform
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

from crab_archi_design.cad_parser import find_oda_converter, find_source_files, run_batch


BG = "#14161c"
SURFACE = "#1b1f28"
SURFACE_2 = "#222733"
SURFACE_3 = "#2a303d"
TEXT = "#f4fdff"
MUTED = "#8b94a7"
CORAL = "#ff795f"
ORANGE = "#ffb26b"
RED = "#ff4f4a"
PURPLE = "#d65cff"
GREEN = "#7fe1b5"
LINE = "#303746"


class CrabCADParserApp:
    """A compact operator console for a local CAD-to-ontology batch."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("CrabCADParser / Command Deck")
        # The dashboard has a live log and a small pipeline summary.  Give it
        # enough initial height for the controls and result action to remain
        # visible on a normal laptop display; the window can still be resized
        # down to the compact layout.
        self.root.geometry("1180x900")
        self.root.minsize(980, 660)
        self.root.configure(bg=BG)

        self.input_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.converter = tk.StringVar()
        self.pack_title = tk.StringVar(value="CrabCADParser CAD Ontology Pack")
        self.recursive = tk.BooleanVar(value=True)
        self.resume = tk.BooleanVar(value=True)
        self.running = False
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()

        self.status = tk.StringVar(value="준비 / 도면 폴더를 선택하세요")
        self.inventory = tk.StringVar(value="아직 선택된 도면 폴더가 없습니다")
        self.converter_state = tk.StringVar(value="AUTO DISCOVERY")
        self.output_state = tk.StringVar(value="No output pack yet")
        self.stat_files = tk.StringVar(value="—")
        self.stat_parsed = tk.StringVar(value="—")
        self.stat_warnings = tk.StringVar(value="—")
        self.stat_pack = tk.StringVar(value="WAITING")
        self.step_vars = {name: tk.StringVar(value="PENDING") for name in ("DISCOVER", "READ", "RECOGNIZE", "PACKAGE")}

        self._build()
        self._refresh_converter_state()

    def _font(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        return ("Helvetica Neue", size, weight)

    def _label(self, parent: tk.Misc, text: str, *, size: int = 10, color: str = TEXT, weight: str = "normal", **kwargs: Any) -> tk.Label:
        return tk.Label(parent, text=text, bg=kwargs.pop("bg", parent.cget("bg")), fg=color, font=self._font(size, weight), **kwargs)

    def _card(self, parent: tk.Misc, *, padx: int = 18, pady: int = 16) -> tk.Frame:
        frame = tk.Frame(parent, bg=SURFACE, highlightbackground=LINE, highlightthickness=1)
        # Let cards grow to the height requested by their children.  Without
        # this, cards that have no explicit height collapse to 1px and hide
        # their path selectors and parse button.
        return frame

    def _build(self) -> None:
        self._build_topbar()
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=(0, 22))

        self._build_sidebar(body)
        content = tk.Frame(body, bg=BG)
        content.pack(side="left", fill="both", expand=True, padx=(22, 0))
        self._build_hero(content)

        workspace = tk.Frame(content, bg=BG)
        workspace.pack(fill="both", expand=True, pady=(18, 0))
        left = tk.Frame(workspace, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(workspace, bg=BG, width=245)
        right.pack(side="right", fill="y", padx=(18, 0))
        right.pack_propagate(False)

        self._build_source_card(left)
        self._build_activity_card(left)
        self._build_pipeline_card(right)
        self._build_stats_card(right)

    def _build_topbar(self) -> None:
        bar = tk.Frame(self.root, bg=BG, height=68)
        bar.pack(fill="x", padx=22, pady=(16, 12))
        bar.pack_propagate(False)

        mark = tk.Canvas(bar, width=38, height=38, bg=BG, highlightthickness=0)
        mark.pack(side="left", padx=(0, 12), pady=8)
        mark.create_oval(9, 9, 29, 29, fill=CORAL, outline="")
        mark.create_line(5, 15, 1, 10, fill=ORANGE, width=2)
        mark.create_line(33, 15, 37, 10, fill=ORANGE, width=2)
        mark.create_line(10, 29, 5, 35, fill=RED, width=2)
        mark.create_line(28, 29, 33, 35, fill=RED, width=2)
        mark.create_oval(14, 14, 17, 17, fill=BG, outline="")
        mark.create_oval(21, 14, 24, 17, fill=BG, outline="")

        title = tk.Frame(bar, bg=BG)
        title.pack(side="left", fill="y")
        self._label(title, "CRABCADPARSER", size=15, weight="bold").pack(anchor="w", pady=(7, 0))
        self._label(title, "DWG / DXF  ·  LOCAL ONTOLOGY WORKSPACE", size=8, color=MUTED, weight="bold").pack(anchor="w", pady=(2, 0))

        badge = tk.Frame(bar, bg=SURFACE_2, highlightbackground=LINE, highlightthickness=1)
        badge.pack(side="right", pady=10)
        tk.Label(badge, text="●", bg=SURFACE_2, fg=GREEN, font=self._font(9, "bold")).pack(side="left", padx=(12, 6), pady=8)
        tk.Label(badge, text="LOCAL  /  OPENCRAB READY", bg=SURFACE_2, fg=TEXT, font=self._font(9, "bold")).pack(side="left", padx=(0, 12), pady=8)

    def _build_sidebar(self, parent: tk.Frame) -> None:
        sidebar = tk.Frame(parent, bg=SURFACE, width=190, highlightbackground=LINE, highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        self._label(sidebar, "WORKSPACE", size=8, color=MUTED, weight="bold", bg=SURFACE).pack(anchor="w", padx=18, pady=(22, 12))
        nav_items = (("01", "IMPORT SET", CORAL), ("02", "CAD IR", TEXT), ("03", "ONTOLOGY", TEXT), ("04", "EXPORT", TEXT))
        for number, label, color in nav_items:
            row = tk.Frame(sidebar, bg=SURFACE_2 if number == "01" else SURFACE, height=42)
            row.pack(fill="x", padx=10, pady=3)
            row.pack_propagate(False)
            tk.Label(row, text=number, bg=row.cget("bg"), fg=color, font=self._font(9, "bold")).pack(side="left", padx=(10, 10))
            tk.Label(row, text=label, bg=row.cget("bg"), fg=TEXT if number == "01" else MUTED, font=self._font(9, "bold")).pack(side="left")

        spacer = tk.Frame(sidebar, bg=SURFACE)
        spacer.pack(fill="both", expand=True)
        converter_card = tk.Frame(sidebar, bg=SURFACE_2, highlightbackground=LINE, highlightthickness=1)
        converter_card.pack(fill="x", padx=10, pady=10)
        self._label(converter_card, "DWG CONVERTER", size=8, color=MUTED, weight="bold", bg=SURFACE_2).pack(anchor="w", padx=12, pady=(12, 4))
        self.converter_badge = self._label(converter_card, self.converter_state.get(), size=9, color=ORANGE, weight="bold", bg=SURFACE_2)
        self.converter_badge.pack(anchor="w", padx=12, pady=(0, 12))

        self._label(sidebar, "v0.2  /  ROUNDTRIP IR", size=8, color=MUTED, bg=SURFACE).pack(anchor="w", padx=18, pady=(0, 18))

    def _build_hero(self, parent: tk.Frame) -> None:
        hero = tk.Frame(parent, bg=BG)
        hero.pack(fill="x")
        self._label(hero, "CAD INGESTION DECK", size=9, color=CORAL, weight="bold", bg=BG).pack(anchor="w")
        self._label(hero, "도면 폴더를 선택하고\n파싱을 시작하세요.", size=24, color=TEXT, weight="bold", bg=BG, justify="left").pack(anchor="w", pady=(5, 3))
        self._label(hero, "DWG/DXF의 선, 문자, 블록, 레이어, 스타일을 읽어 다시 설계할 수 있는 데이터로 저장합니다.", size=10, color=MUTED, bg=BG).pack(anchor="w")

    def _section_head(self, parent: tk.Frame, eyebrow: str, title: str, *, bg: str = SURFACE) -> tk.Frame:
        head = tk.Frame(parent, bg=bg)
        head.pack(fill="x")
        self._label(head, eyebrow, size=8, color=CORAL, weight="bold", bg=bg).pack(anchor="w")
        self._label(head, title, size=14, color=TEXT, weight="bold", bg=bg).pack(anchor="w", pady=(3, 0))
        return head

    def _build_source_card(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        card.pack(fill="x")
        self._section_head(card, "1 · INPUT", "도면 폴더를 선택하세요")
        self._label(card, "① 도면 폴더 선택  →  ② 결과 폴더 확인  →  ③ 파싱 시작", size=10, color=ORANGE, weight="bold", bg=SURFACE).pack(anchor="w", pady=(6, 0))

        self._path_row(card, "입력 도면 폴더", self.input_dir, self.choose_input, "도면 폴더 선택", "input_button")
        self._path_row(card, "결과 저장 폴더", self.output_dir, self.choose_output, "결과 폴더 선택", "output_button")

        inventory = tk.Frame(card, bg=SURFACE_2, height=34)
        inventory.pack(fill="x", pady=(6, 6))
        inventory.pack_propagate(False)
        tk.Label(inventory, text="◈", bg=SURFACE_2, fg=ORANGE, font=self._font(11, "bold")).pack(side="left", padx=(12, 8))
        self._label(inventory, self.inventory.get(), size=9, color=MUTED, bg=SURFACE_2).pack(side="left")

        fields = tk.Frame(card, bg=SURFACE)
        fields.pack(fill="x")
        left = tk.Frame(fields, bg=SURFACE)
        left.pack(side="left", fill="x", expand=True, padx=(0, 8))
        right = tk.Frame(fields, bg=SURFACE)
        right.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._text_field(left, "팩 이름", self.pack_title)
        self._text_field(right, "ODA 변환기 경로 (선택)", self.converter, trace=True)

        options = tk.Frame(card, bg=SURFACE)
        options.pack(fill="x", pady=(7, 0))
        self._check(options, "하위 폴더까지 찾기", self.recursive).pack(side="left", padx=(0, 20))
        self._check(options, "이미 처리한 파일 재사용", self.resume).pack(side="left")
        self.start_button = tk.Button(options, text="▶  파싱 시작", command=self.start, bg=CORAL, fg=BG, activebackground=ORANGE, activeforeground=BG, relief="flat", bd=0, padx=22, pady=10, font=self._font(10, "bold"), cursor="hand2")
        self.start_button.pack(side="right")

    def _build_activity_card(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        card.pack(fill="both", expand=True, pady=(18, 0))
        head = self._section_head(card, "2 · PROGRESS", "파싱 진행 상황")
        self.status_label = self._label(head, self.status.get(), size=9, color=ORANGE, bg=SURFACE)
        self.status_label.pack(anchor="w", pady=(7, 0))

        progress_row = tk.Frame(card, bg=SURFACE)
        progress_row.pack(fill="x", pady=(14, 10))
        self.progress_canvas = tk.Canvas(progress_row, height=7, bg=SURFACE_3, highlightthickness=0)
        self.progress_canvas.pack(fill="x", expand=True)
        self.progress_canvas.bind("<Configure>", lambda _event: self._draw_progress())
        self.progress_value = 0.0

        log_frame = tk.Frame(card, bg="#101218", highlightbackground=LINE, highlightthickness=1)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=3, wrap="word", state="disabled", bg="#101218", fg="#b8c0cf", insertbackground=TEXT, selectbackground="#47313b", relief="flat", bd=0, padx=12, pady=10, font=("Menlo", 9))
        self.log.pack(fill="both", expand=True)

        footer = tk.Frame(card, bg=SURFACE)
        footer.pack(fill="x", pady=(10, 0))
        self._label(footer, "결과 팩", size=8, color=MUTED, weight="bold", bg=SURFACE).pack(side="left")
        self.output_label = self._label(footer, self.output_state.get(), size=8, color=MUTED, bg=SURFACE)
        self.output_label.pack(side="left", padx=(10, 0))
        self.open_button = tk.Button(footer, text="결과 열기", command=self.open_output, state="disabled", bg=SURFACE_2, fg=TEXT, activebackground=SURFACE_3, activeforeground=TEXT, relief="flat", bd=0, padx=12, pady=6, font=self._font(9, "bold"), cursor="hand2")
        self.open_button.pack(side="right")

    def _build_pipeline_card(self, parent: tk.Frame) -> None:
        card = self._card(parent, padx=16, pady=16)
        card.pack(fill="x")
        self._section_head(card, "3 · PIPELINE", "팩을 만드는 4단계")
        self.pipeline_rows: dict[str, tuple[tk.Label, tk.Label]] = {}
        descriptions = {
            "DISCOVER": "도면 목록 확인",
            "READ": "CAD 형상 읽기",
            "RECOGNIZE": "설계 역할 추정",
            "PACKAGE": "IR + 근거 저장",
        }
        for number, name in enumerate(("DISCOVER", "READ", "RECOGNIZE", "PACKAGE"), 1):
            row = tk.Frame(card, bg=SURFACE)
            row.pack(fill="x", pady=(15 if number == 1 else 10, 0))
            dot = tk.Label(row, text=f"0{number}", width=3, bg=SURFACE_2, fg=MUTED, font=self._font(8, "bold"))
            dot.pack(side="left", padx=(0, 9), ipady=4)
            text = tk.Frame(row, bg=SURFACE)
            text.pack(side="left", fill="x", expand=True)
            title = self._label(text, name, size=9, color=TEXT, weight="bold", bg=SURFACE)
            title.pack(anchor="w")
            self._label(text, descriptions[name], size=8, color=MUTED, bg=SURFACE).pack(anchor="w", pady=(2, 0))
            state = self._label(row, self.step_vars[name].get(), size=8, color=MUTED, weight="bold", bg=SURFACE)
            state.pack(side="right")
            self.pipeline_rows[name] = (dot, state)

        divider = tk.Frame(card, bg=LINE, height=1)
        divider.pack(fill="x", pady=(18, 14))
        self._label(card, "다시 그리기 품질", size=8, color=MUTED, weight="bold", bg=SURFACE).pack(anchor="w")
        self._label(card, "구조화 재생 + 표준 DXF 보존본", size=9, color=TEXT, bg=SURFACE, wraplength=210, justify="left").pack(anchor="w", pady=(5, 0))

    def _build_stats_card(self, parent: tk.Frame) -> None:
        card = self._card(parent, padx=16, pady=16)
        card.pack(fill="x", pady=(18, 0))
        self._section_head(card, "BATCH SNAPSHOT", "현재 실행 요약")
        self._stat_row(card, "FILES", self.stat_files)
        self._stat_row(card, "PARSED", self.stat_parsed)
        self._stat_row(card, "WARNINGS", self.stat_warnings)
        self._stat_row(card, "PACK", self.stat_pack, last=True)

    def _path_row(self, parent: tk.Frame, label: str, variable: tk.StringVar, callback: Any, button_text: str, button_attr: str) -> None:
        row = tk.Frame(parent, bg=SURFACE_2, highlightbackground=LINE, highlightthickness=1)
        row.pack(fill="x", pady=(8, 0))
        self._label(row, label, size=8, color=MUTED, weight="bold", bg=SURFACE_2).pack(side="left", padx=(12, 10))
        entry = tk.Entry(row, textvariable=variable, bg=SURFACE_2, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0, font=self._font(9))
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=9)
        button = tk.Button(row, text=button_text, command=callback, bg=SURFACE_3, fg=TEXT, activebackground=LINE, activeforeground=TEXT, relief="flat", bd=0, padx=13, pady=7, font=self._font(9, "bold"), cursor="hand2")
        button.pack(side="right", padx=6)
        setattr(self, button_attr, button)

    def _text_field(self, parent: tk.Frame, label: str, variable: tk.StringVar, *, trace: bool = False) -> None:
        self._label(parent, label, size=8, color=MUTED, weight="bold", bg=SURFACE).pack(anchor="w", pady=(0, 5))
        entry = tk.Entry(parent, textvariable=variable, bg=SURFACE_2, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0, font=self._font(9))
        entry.pack(fill="x", ipady=6)
        if trace:
            variable.trace_add("write", lambda *_args: self._refresh_converter_state())

    def _check(self, parent: tk.Frame, text: str, variable: tk.BooleanVar) -> tk.Frame:
        holder = tk.Frame(parent, bg=SURFACE)
        check = tk.Checkbutton(holder, text="✓", variable=variable, onvalue=True, offvalue=False, bg=SURFACE, fg=CORAL, selectcolor=SURFACE_2, activebackground=SURFACE, activeforeground=ORANGE, relief="flat", bd=0, highlightthickness=0, font=self._font(10, "bold"))
        check.pack(side="left")
        self._label(holder, text, size=8, color=MUTED, weight="bold", bg=SURFACE).pack(side="left", padx=(2, 0))
        return holder

    def _stat_row(self, parent: tk.Frame, label: str, variable: tk.StringVar, *, last: bool = False) -> None:
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=(14 if label == "FILES" else 9, 0 if last else 1))
        self._label(row, label, size=8, color=MUTED, weight="bold", bg=SURFACE).pack(side="left")
        self._label(row, variable.get(), size=13, color=TEXT if label != "PACK" else ORANGE, weight="bold", bg=SURFACE).pack(side="right")
        variable.trace_add("write", lambda *_args, target=row, value=variable, name=label: self._update_stat_label(target, value, name))

    def _update_stat_label(self, row: tk.Frame, variable: tk.StringVar, name: str) -> None:
        labels = [item for item in row.winfo_children() if isinstance(item, tk.Label)]
        if labels:
            labels[-1].configure(text=variable.get())

    def choose_input(self) -> None:
        chosen = filedialog.askdirectory(title="DWG/DXF 도면 폴더 선택")
        if chosen:
            self.input_dir.set(chosen)
            if not self.output_dir.get():
                source = Path(chosen)
                self.output_dir.set(str(source.parent / f"{source.name}_crabcadparser"))
            self.refresh_inventory()

    def choose_output(self) -> None:
        chosen = filedialog.askdirectory(title="결과를 저장할 폴더 선택", mustexist=False)
        if chosen:
            self.output_dir.set(chosen)

    def refresh_inventory(self) -> None:
        input_path = Path(self.input_dir.get()).expanduser()
        if not input_path.is_dir():
            self.inventory.set("아직 선택된 도면 폴더가 없습니다")
            return
        try:
            files = find_source_files(input_path, recursive=self.recursive.get())
            dwg = sum(1 for item in files if item.suffix.lower() == ".dwg")
            dxf = len(files) - dwg
            self.inventory.set(f"{len(files):,}개 파일  /  DWG {dwg:,}  /  DXF {dxf:,}  /  준비 완료")
            self.stat_files.set(f"{len(files):,}")
            self._set_step("DISCOVER", "READY", ORANGE)
        except Exception as exc:
            self.inventory.set(f"Inventory error  /  {exc}")

    def _refresh_converter_state(self) -> None:
        converter = find_oda_converter(self.converter.get() or None)
        if converter:
            self.converter_state.set("FOUND / ODA")
            color = GREEN
        elif self.converter.get().strip():
            self.converter_state.set("PATH NOT FOUND")
            color = RED
        else:
            self.converter_state.set("AUTO DISCOVERY")
            color = ORANGE
        if hasattr(self, "converter_badge"):
            self.converter_badge.configure(text=self.converter_state.get(), fg=color)

    def _set_step(self, name: str, state: str, color: str) -> None:
        self.step_vars[name].set(state)
        if name in getattr(self, "pipeline_rows", {}):
            dot, label = self.pipeline_rows[name]
            dot.configure(fg=color, bg=SURFACE_3 if state in {"ACTIVE", "READY", "DONE"} else SURFACE_2)
            label.configure(text=state, fg=color)

    def _draw_progress(self) -> None:
        if not hasattr(self, "progress_canvas"):
            return
        self.progress_canvas.delete("all")
        width = max(1, self.progress_canvas.winfo_width())
        self.progress_canvas.create_rectangle(0, 0, width, 7, fill=SURFACE_3, outline="")
        self.progress_canvas.create_rectangle(0, 0, int(width * max(0.0, min(1.0, self.progress_value))), 7, fill=CORAL, outline="")

    def append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self) -> None:
        if self.running:
            return
        input_dir = Path(self.input_dir.get()).expanduser()
        if not input_dir.is_dir():
            messagebox.showerror("INPUT SET REQUIRED", "DWG/DXF 파일이 있는 입력 폴더를 먼저 선택하세요.")
            return
        files = find_source_files(input_dir, recursive=self.recursive.get())
        if not files:
            messagebox.showerror("NO CAD FILES", "선택한 폴더에서 DWG/DXF 파일을 찾지 못했습니다.")
            return
        output_dir = Path(self.output_dir.get()).expanduser() if self.output_dir.get() else input_dir.parent / f"{input_dir.name}_crabcadparser"
        self.output_dir.set(str(output_dir))
        self.running = True
        self.open_button.configure(state="disabled")
        self.start_button.configure(state="disabled", text="파싱 중  …")
        self.progress_value = 0.0
        self._draw_progress()
        self.status.set("시작 중 / 도면 파일을 확인하고 있습니다")
        self.status_label.configure(text=self.status.get(), fg=ORANGE)
        self.stat_files.set(f"{len(files):,}")
        self.stat_parsed.set("0")
        self.stat_warnings.set("0")
        self.stat_pack.set("BUILDING")
        for name in self.step_vars:
            self._set_step(name, "PENDING", MUTED)
        self._set_step("DISCOVER", "DONE", GREEN)
        self.append_log(f"SOURCE  {input_dir}")
        self.append_log(f"OUTPUT  {output_dir}")
        args = {
            "recursive": bool(self.recursive.get()),
            "converter": self.converter.get() or None,
            "resume": bool(self.resume.get()),
            "pack_title": self.pack_title.get() or None,
        }
        thread = threading.Thread(target=self._run, args=(input_dir, output_dir, args), daemon=True)
        thread.start()
        self.root.after(50, self._drain_progress)

    def _run(self, input_dir: Path, output_dir: Path, args: dict[str, Any]) -> None:
        def progress(event: dict[str, Any]) -> None:
            # Tk is not thread-safe on macOS.  Queue events here and let the
            # main loop update widgets from _drain_progress.
            self._events.put(event)

        try:
            run_batch(input_dir, output_dir, progress=progress, **args)
        except Exception as exc:
            self._events.put({"stage": "error", "error": str(exc)})

    def _drain_progress(self) -> None:
        drained = False
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            drained = True
            if event.get("stage") == "error":
                self._finish_error(str(event.get("error") or "Unknown parser error"))
            else:
                self._handle_progress(event)
        if self.running or drained:
            self.root.after(50, self._drain_progress)

    def _handle_progress(self, event: dict[str, Any]) -> None:
        if event.get("stage") == "file":
            total = int(event.get("total") or 1)
            index = int(event.get("index") or 0)
            self.progress_value = index / max(1, total)
            self._draw_progress()
            state = str(event.get("status") or "")
            relative = event.get("relative_path") or event.get("source_path")
            self.status.set(f"{index:04d} / {total:04d}  ·  {state.upper()}  ·  {relative}")
            self.status_label.configure(text=self.status.get(), fg=ORANGE if state in {"started", "resumed"} else GREEN if state == "active" else RED)
            self._set_step("READ", "ACTIVE", CORAL)
            self._set_step("RECOGNIZE", "ACTIVE", PURPLE)
            self.append_log(f"{index:04d}/{total:04d}  {state.upper():16}  {relative}")
            if event.get("error"):
                self.append_log(f"         ! {event['error']}")
                self.stat_warnings.set(str(int(self.stat_warnings.get() or "0") + 1))
        elif event.get("stage") == "complete":
            stats = event.get("statistics") or {}
            self.progress_value = 1.0
            self._draw_progress()
            self._set_step("READ", "DONE", GREEN)
            self._set_step("RECOGNIZE", "DONE", GREEN)
            self._set_step("PACKAGE", "DONE", GREEN)
            self.status.set("완료 / 결과 팩과 다시 그리기용 DXF가 준비되었습니다")
            self.status_label.configure(text=self.status.get(), fg=GREEN)
            self.stat_parsed.set(f"{int(stats.get('parsed', 0)):,}")
            self.stat_warnings.set(f"{int(stats.get('warnings', 0)):,}")
            self.stat_pack.set("PASS" if int(stats.get("failed", 0)) == 0 else "REVIEW")
            self.output_state.set(str(event.get("pack_dir") or "Pack generated"))
            self.output_label.configure(text=self.output_state.get(), fg=GREEN if self.stat_pack.get() == "PASS" else ORANGE)
            self.append_log(json.dumps(stats, ensure_ascii=False))
            self.append_log(f"PACK  {event.get('pack_dir')}")
            self.append_log(f"ZIP   {event.get('pack_zip')}")
            self.running = False
            self.start_button.configure(state="normal", text="▶  파싱 시작")
            self.open_button.configure(state="normal")

    def _finish_error(self, detail: str) -> None:
        self.running = False
        self.start_button.configure(state="normal", text="▶  파싱 시작")
        self.status.set("오류 / 아래 실행 기록을 확인하세요")
        self.status_label.configure(text=self.status.get(), fg=RED)
        self.stat_pack.set("ERROR")
        self.append_log(f"ERROR  {detail}")
        messagebox.showerror("CrabCADParser ERROR", detail)

    def open_output(self) -> None:
        path = Path(self.output_state.get())
        if path.exists():
            self.open_path(path)

    @staticmethod
    def open_path(path: Path) -> None:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif system == "Windows":
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


def launch() -> None:
    root = tk.Tk()
    CrabCADParserApp(root)
    root.after(100, root.lift)
    root.after(120, root.focus_force)
    root.mainloop()


if __name__ == "__main__":
    launch()
