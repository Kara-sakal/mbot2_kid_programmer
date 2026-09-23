from __future__ import annotations

import json
import os
from email.policy import default
from pathlib import Path
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import messagebox, filedialog
from typing import Any, Literal, Optional

from mbot2_uploader import upload_to_mbot2, test_connection

KID_UPLOAD = False

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "blocks.json")

BLOCK_W = 150
BLOCK_H = 72
PARAM_W = 66
ROW_GAP = 18
INDENT = 46
PALETTE_GAP = 12
PARAM_AREA_W = 5 * (PARAM_W + PALETTE_GAP)
COMMAND_PALETTE_COLS = 3
PARAM_PALETTE_COLS = 5
PALETTE_WIDTH = 510

PROGRAM_X = 30
PROGRAM_Y = 24
PROGRAM_RIGHT = 620
EMPTY_CHILD_H = 58

BlockKind = Literal["command", "container", "parameter"]


@dataclass
class BlockDef:
    id: str
    kind: BlockKind
    icon: str = "?"
    label: str = ""
    color: str = "#d9eaf7"
    imports: list[str] = field(default_factory=list)
    code: str = ""
    header: str = ""
    defaults: list[Any] = field(default_factory=list)
    value: Any = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BlockDef":
        return cls(
            id=str(data["id"]),
            kind=data.get("kind", "command"),
            icon=str(data.get("icon", "?")),
            label=str(data.get("label", "")),
            color=str(data.get("color", "#d9eaf7")),
            imports=[str(x) for x in data.get("imports", [])],
            code=str(data.get("code", "")),
            header=str(data.get("header", "")),
            defaults=list(data.get("defaults", [])),
            value=data.get("value"),
        )


@dataclass
class ProgramNode:
    block_id: str
    params: list[str] = field(default_factory=list)
    children: list["ProgramNode"] = field(default_factory=list)


@dataclass
class LayoutRow:
    node: ProgramNode
    parent: list[ProgramNode]
    index: int
    depth: int
    y: float
    visual_bottom: float


@dataclass
class InsertionZone:
    parent: list[ProgramNode]
    index: int
    depth: int
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class ChildArea:
    container: ProgramNode
    depth: int
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class DragState:
    source_type: Literal["palette", "main", "param"]
    block_id: str
    row_index: Optional[int] = None
    param_index: Optional[int] = None


class IconProgrammer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title("mBot2 Icon Programmer")
        self.geometry("1260x780")
        self.minsize(1040, 650)

        self.blocks: dict[str, BlockDef] = self.load_blocks()
        self.block_order: list[str] = list(self.blocks.keys())
        self.program: list[ProgramNode] = []
        self.current_program_file: Optional[str] = None

        self.layout_rows: list[LayoutRow] = []
        self.insertion_zones: list[InsertionZone] = []
        self.child_areas: list[ChildArea] = []
        self.row_by_node: dict[int, int] = {}

        self.drag_state: Optional[DragState] = None
        self.drag_ghost: Optional[tk.Toplevel] = None
        self.drag_offset_x = 0
        self.drag_offset_y = 0

        self.palette_canvas: tk.Canvas
        self.palette_scrollbar: tk.Scrollbar
        self.program_canvas: tk.Canvas
        self.program_scrollbar: tk.Scrollbar
        self.code_text: tk.Text
        self.code_scrollbar: tk.Scrollbar
        self.status_label: tk.Label

        self.build_ui()
        self.redraw_all()

    @staticmethod
    def load_blocks() -> dict[str, BlockDef]:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            raw: dict[str, Any] = json.load(file)

        result: dict[str, BlockDef] = {}
        for item in raw.get("blocks", []):
            block = BlockDef.from_dict(item)
            result[block.id] = block
        return result

    def build_ui(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=2, minsize=400, uniform="workspace")
        self.columnconfigure(2, weight=1, minsize=200, uniform="workspace")
        self.rowconfigure(1, weight=1)

        tk.Label(
            self,
            text=(
                "Drop inside a real LOOP/IF child area to nest. "
                "Drop in a gap to insert exactly between blocks."
            ),
            font=("Segoe UI", 12, "bold"),
            anchor="w",
            padx=12,
            pady=10,
        ).grid(row=0, column=0, columnspan=3, sticky="ew")

        # ---------- BLOCKS ----------
        left = tk.Frame(self, bd=1, relief="solid")
        left.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=(0, 10))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        tk.Label(
            left,
            text="BLOCKS",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, columnspan=2, pady=8)

        self.palette_canvas = tk.Canvas(
            left,
            width=PALETTE_WIDTH,
            bg="white",
            highlightthickness=0,
            yscrollincrement=18,
        )
        self.palette_scrollbar = tk.Scrollbar(
            left,
            orient="vertical",
            command=self.palette_canvas.yview,
        )
        self.palette_canvas.configure(
            yscrollcommand=self.palette_scrollbar.set
        )

        self.palette_canvas.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=(6, 0),
            pady=(0, 6),
        )
        self.palette_scrollbar.grid(
            row=1,
            column=1,
            sticky="ns",
            padx=(0, 6),
            pady=(0, 6),
        )

        self.palette_canvas.bind("<ButtonPress-1>", self.on_palette_press)
        self.palette_canvas.bind("<B1-Motion>", self.on_drag_motion)
        self.palette_canvas.bind("<ButtonRelease-1>", self.on_drag_release)
        self.bind_vertical_mousewheel(self.palette_canvas)

        # ---------- PROGRAM ----------
        middle = tk.Frame(self, bd=1, relief="solid")
        middle.grid(row=1, column=1, sticky="nsew", padx=5, pady=(0, 10))
        middle.rowconfigure(2, weight=1)
        middle.columnconfigure(0, weight=1)

        tk.Label(
            middle,
            text="MY PROGRAM",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, columnspan=2, pady=(8, 4))

        # Project controls belong to the visual-program section.
        program_buttons = tk.Frame(middle)
        program_buttons.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            padx=8,
            pady=(0, 8),
        )

        button_style = {
            "font": ("Segoe UI", 11, "bold"),
            "padx": 12,
            "pady": 6,
        }

        tk.Button(
            program_buttons,
            text="📂  Open",
            command=self.open_visual_program,
            **button_style,
        ).pack(side="left")

        tk.Button(
            program_buttons,
            text="💾  Save",
            command=self.save_visual_program,
            **button_style,
        ).pack(side="left", padx=(8, 0))

        tk.Button(
            program_buttons,
            text="💾  Save As",
            command=self.save_visual_program_as,
            **button_style,
        ).pack(side="left", padx=(8, 0))

        tk.Button(
            program_buttons,
            text="✖  Clear",
            command=self.clear_program,
            **button_style,
        ).pack(side="left", padx=(16, 0))

        self.program_canvas = tk.Canvas(
            middle,
            bg="#f7f7f7",
            highlightthickness=0,
            yscrollincrement=18,
        )
        self.program_scrollbar = tk.Scrollbar(
            middle,
            orient="vertical",
            command=self.program_canvas.yview,
        )
        self.program_canvas.configure(
            yscrollcommand=self.program_scrollbar.set
        )

        self.program_canvas.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=(6, 0),
            pady=(0, 6),
        )
        self.program_scrollbar.grid(
            row=2,
            column=1,
            sticky="ns",
            padx=(0, 6),
            pady=(0, 6),
        )

        self.program_canvas.bind("<ButtonPress-1>", self.on_program_press)
        self.program_canvas.bind("<B1-Motion>", self.on_drag_motion)
        self.program_canvas.bind("<ButtonRelease-1>", self.on_drag_release)
        self.bind_vertical_mousewheel(self.program_canvas)

        # ---------- CODE ----------
        right = tk.Frame(self, bd=1, relief="solid")
        right.grid(row=1, column=2, sticky="nsew", padx=(5, 10), pady=(0, 10))
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        tk.Label(
            right,
            text="PYTHON FOR mBLOCK",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, columnspan=2, pady=(8, 4))

        # Code-specific action belongs to the code section.
        code_buttons = tk.Frame(right)
        code_buttons.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            padx=8,
            pady=(0, 8),
        )

        tk.Button(
            code_buttons,
            text="📋  Copy code",
            command=self.copy_code,
            **button_style,
        ).pack(side="left")

        tk.Button(
            code_buttons,
            text="⬆  Upload",
            command=self.do_upload,
            bg="#39c85a",
            activebackground="#28a947",
            **button_style,
        ).pack(side="left", padx=(16, 0))

        tk.Button(
            code_buttons,
            text="🔌  Test connection",
            command=self.do_test_connection,
            **button_style,
        ).pack(side="left", padx=(8, 0))

        self.code_text = tk.Text(
            right,
            wrap="none",
            font=("Consolas", 10),
        )
        self.code_scrollbar = tk.Scrollbar(
            right,
            orient="vertical",
            command=self.code_text.yview,
        )
        self.code_text.configure(
            yscrollcommand=self.code_scrollbar.set
        )

        self.code_text.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=(6, 0),
            pady=(0, 6),
        )
        self.code_scrollbar.grid(
            row=2,
            column=1,
            sticky="ns",
            padx=(0, 6),
            pady=(0, 6),
        )
        self.bind_vertical_mousewheel(self.code_text)

        # ---------- BIG CHILD-FRIENDLY UPLOAD ----------
        # This duplicates the normal Upload button intentionally.  Both buttons
        # call exactly the same do_upload() method.
        if KID_UPLOAD:
            kid_upload_area = tk.Frame(
                self,
                bd=2,
                relief="groove",
                bg="#dff4ff",
                padx=12,
                pady=10,
            )
            kid_upload_area.grid(
                row=2,
                column=0,
                columnspan=3,
                sticky="ew",
                padx=10,
                pady=(0, 4),
            )
            kid_upload_area.columnconfigure(0, weight=1)

            tk.Label(
                kid_upload_area,
                text="PROGRAM READY?",
                font=("Segoe UI", 11, "bold"),
                bg="#dff4ff",
                fg="#174a67",
            ).grid(row=0, column=0, pady=(0, 5))

            self.kid_upload_button = tk.Button(
                kid_upload_area,
                text="🚀   UPLOAD TO ROBOT   🚀\nRun my program!",
                command=self.do_upload,
                font=("Segoe UI", 18, "bold"),
                bg="#39c85a",
                fg="white",
                activebackground="#28a947",
                activeforeground="white",
                relief="raised",
                bd=5,
                padx=35,
                pady=10,
                cursor="hand2",
            )
            self.kid_upload_button.grid(
                row=1,
                column=0,
                sticky="ew",
                padx=80,
            )

        self.status_label = tk.Label(
            self,
            text="",
            anchor="w",
            padx=12,
            pady=4,
        )
        self.status_label.grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="ew",
        )

    @staticmethod
    def bind_vertical_mousewheel(widget: tk.Widget) -> None:
        """Make a widget vertically scrollable with the mouse wheel."""
        def on_mousewheel(event: tk.Event) -> str:
            delta = getattr(event, "delta", 0)
            if delta:
                widget.yview_scroll(int(-delta / 120), "units")
            return "break"

        def on_linux_up(_event: tk.Event) -> str:
            widget.yview_scroll(-1, "units")
            return "break"

        def on_linux_down(_event: tk.Event) -> str:
            widget.yview_scroll(1, "units")
            return "break"

        widget.bind("<MouseWheel>", on_mousewheel)
        widget.bind("<Button-4>", on_linux_up)
        widget.bind("<Button-5>", on_linux_down)

    def reload_blocks(self) -> None:
        try:
            self.blocks = self.load_blocks()
            self.block_order = list(self.blocks.keys())
            self.program.clear()
            self.redraw_all()
            self.status_label.config(text="Reloaded blocks.json")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            messagebox.showerror("Could not reload blocks.json", str(exc))

    def redraw_all(self) -> None:
        self.redraw_palette()
        self.redraw_program()
        self.update_code()

    def draw_block(
        self,
        canvas: tk.Canvas,
        x: float,
        y: float,
        block: BlockDef,
        tag: str,
        parameter: bool = False,
    ) -> None:
        width = PARAM_W if parameter else BLOCK_W

        canvas.create_rectangle(
            x,
            y,
            x + width,
            y + BLOCK_H,
            fill=block.color,
            outline="#333333",
            width=2,
            tags=(tag,),
        )

        if parameter:
            canvas.create_text(
                x + width / 2,
                y + BLOCK_H / 2,
                text=block.label or block.icon,
                font=("Segoe UI", 18, "bold"),
                tags=(tag,),
            )
            return

        canvas.create_text(
            x + width / 2,
            y + 24,
            text=block.icon,
            font=("Segoe UI Symbol", 22, "bold"),
            tags=(tag,),
        )
        canvas.create_text(
            x + width / 2,
            y + 53,
            text=block.label,
            width=width - 10,
            font=("Segoe UI", 10, "bold"),
            tags=(tag,),
        )

    def redraw_palette(self) -> None:
        self.palette_canvas.delete("all")

        command_ids = [
            block_id
            for block_id in self.block_order
            if self.blocks[block_id].kind != "parameter"
        ]
        parameter_ids = [
            block_id
            for block_id in self.block_order
            if self.blocks[block_id].kind == "parameter"
        ]

        y = 8

        self.palette_canvas.create_text(
            8,
            y,
            text="COMMANDS",
            anchor="nw",
            font=("Segoe UI", 9, "bold"),
            fill="#666666",
        )
        y += 24

        for position, block_id in enumerate(command_ids):
            block = self.blocks[block_id]
            col = position % COMMAND_PALETTE_COLS
            row = position // COMMAND_PALETTE_COLS

            x = 8 + col * (BLOCK_W + PALETTE_GAP)
            by = y + row * (BLOCK_H + PALETTE_GAP)

            self.draw_block(
                self.palette_canvas,
                x,
                by,
                block,
                f"palette:{block_id}",
                parameter=False,
            )

        command_rows = (
            len(command_ids) + COMMAND_PALETTE_COLS - 1
        ) // COMMAND_PALETTE_COLS
        y += command_rows * (BLOCK_H + PALETTE_GAP) + 16

        self.palette_canvas.create_text(
            8,
            y,
            text="PARAMETERS",
            anchor="nw",
            font=("Segoe UI", 9, "bold"),
            fill="#666666",
        )
        y += 24

        for position, block_id in enumerate(parameter_ids):
            block = self.blocks[block_id]
            col = position % PARAM_PALETTE_COLS
            row = position // PARAM_PALETTE_COLS

            x = 8 + col * (PARAM_W + PALETTE_GAP)
            by = y + row * (BLOCK_H + PALETTE_GAP)

            self.draw_block(
                self.palette_canvas,
                x,
                by,
                block,
                f"palette:{block_id}",
                parameter=True,
            )

        parameter_rows = (
            len(parameter_ids) + PARAM_PALETTE_COLS - 1
        ) // PARAM_PALETTE_COLS
        bottom = y + parameter_rows * (BLOCK_H + PALETTE_GAP) + 16

        self.palette_canvas.configure(
            scrollregion=(0, 0, PALETTE_WIDTH, bottom)
        )

    def build_layout(self) -> float:
        self.layout_rows = []
        self.insertion_zones = []
        self.child_areas = []
        self.row_by_node = {}

        def layout_list(
            nodes: list[ProgramNode],
            depth: int,
            y_start: float,
        ) -> float:
            x = PROGRAM_X + depth * INDENT
            y = y_start

            # Explicit gap before the first sibling.
            self.insertion_zones.append(
                InsertionZone(
                    parent=nodes,
                    index=0,
                    depth=depth,
                    x1=x - 12,
                    y1=max(PROGRAM_Y - 10, y - ROW_GAP / 2),
                    x2=PROGRAM_RIGHT,
                    y2=y + ROW_GAP / 2,
                )
            )

            for index, node in enumerate(nodes):
                header_y = y
                block = self.blocks[node.block_id]

                row_index = len(self.layout_rows)
                placeholder = LayoutRow(
                    node=node,
                    parent=nodes,
                    index=index,
                    depth=depth,
                    y=header_y,
                    visual_bottom=header_y + BLOCK_H,
                )
                self.layout_rows.append(placeholder)
                self.row_by_node[id(node)] = row_index

                header_bottom = header_y + BLOCK_H

                if block.kind == "container":
                    child_x = PROGRAM_X + (depth + 1) * INDENT
                    child_start = header_bottom + ROW_GAP

                    if node.children:
                        child_bottom = layout_list(
                            node.children,
                            depth + 1,
                            child_start,
                        )
                    else:
                        child_bottom = child_start + EMPTY_CHILD_H

                        # Empty container still has a real insertion target.
                        self.insertion_zones.append(
                            InsertionZone(
                                parent=node.children,
                                index=0,
                                depth=depth + 1,
                                x1=child_x - 12,
                                y1=child_start,
                                x2=PROGRAM_RIGHT - 12,
                                y2=child_bottom,
                            )
                        )

                    visual_bottom = child_bottom + BLOCK_H
                    self.child_areas.append(
                        ChildArea(
                            container=node,
                            depth=depth + 1,
                            x1=child_x - 12,
                            y1=child_start - ROW_GAP / 2,
                            x2=PROGRAM_RIGHT - 8,
                            y2=visual_bottom,
                        )
                    )
                else:
                    visual_bottom = header_bottom

                self.layout_rows[row_index].visual_bottom = visual_bottom
                y = visual_bottom + ROW_GAP

                # Gap after this sibling / before the next sibling.
                self.insertion_zones.append(
                    InsertionZone(
                        parent=nodes,
                        index=index + 1,
                        depth=depth,
                        x1=x - 12,
                        y1=visual_bottom,
                        x2=PROGRAM_RIGHT,
                        y2=y,
                    )
                )

            return y - ROW_GAP

        if not self.program:
            return PROGRAM_Y + 120

        return layout_list(self.program, 0, PROGRAM_Y)

    def redraw_program(self) -> None:
        self.program_canvas.delete("all")
        bottom = self.build_layout()

        if not self.layout_rows:
            self.program_canvas.create_text(
                240,
                80,
                text="DRAG COMMANDS HERE",
                font=("Segoe UI", 18, "bold"),
                fill="#999999",
            )
            self.program_canvas.create_text(
                240,
                118,
                text="Nested blocks are shifted right →",
                font=("Segoe UI", 11),
                fill="#888888",
            )
            return

        # Draw container rectangles first so block rectangles stay on top.
        for row_index, row in enumerate(self.layout_rows):
            block = self.blocks[row.node.block_id]
            if block.kind != "container":
                continue

            x = PROGRAM_X + row.depth * INDENT
            child_x = PROGRAM_X + (row.depth + 1) * INDENT
            child_y = row.y + BLOCK_H + ROW_GAP

            self.program_canvas.create_rectangle(
                x - 10,
                row.y - 6,
                PROGRAM_RIGHT,
                row.visual_bottom + 6,
                outline="#777777",
                width=2,
                dash=(5, 3),
                tags=(f"container:{row_index}",),
            )

        # Insertion zones are intentionally invisible; they are used only for hit testing.

        # Draw blocks and parameters.
        for row_index, row in enumerate(self.layout_rows):
            node = row.node
            block = self.blocks[node.block_id]
            x = PROGRAM_X + row.depth * INDENT
            y = row.y

            self.draw_block(
                self.program_canvas,
                x,
                y,
                block,
                f"main:{row_index}",
            )

            param_x = x + BLOCK_W + 22
            self.program_canvas.create_rectangle(
                param_x - 4,
                y + 3,
                param_x + PARAM_AREA_W,
                y + BLOCK_H - 3,
                outline="#bbbbbb",
                dash=(3, 3),
                tags=(f"paramzone:{row_index}",),
            )

            for param_index, param_id in enumerate(node.params):
                param = self.blocks[param_id]
                px = param_x + param_index * (PARAM_W + PALETTE_GAP)
                self.draw_block(
                    self.program_canvas,
                    px,
                    y,
                    param,
                    f"param:{row_index}:{param_index}",
                    parameter=True,
                )

        self.program_canvas.configure(
            scrollregion=(0, 0, 680, max(bottom + 80, 300))
        )

    def start_drag_ghost(self, block_id: str, x_root: int, y_root: int) -> None:
        self.destroy_drag_ghost()

        block = self.blocks[block_id]
        parameter = block.kind == "parameter"
        width = PARAM_W if parameter else BLOCK_W

        ghost = tk.Toplevel(self)
        ghost.overrideredirect(True)
        ghost.attributes("-topmost", True)
        try:
            ghost.attributes("-alpha", 0.82)
        except tk.TclError:
            pass

        frame = tk.Frame(
            ghost,
            bg=block.color,
            bd=2,
            relief="solid",
            width=width,
            height=BLOCK_H,
        )
        frame.pack()
        frame.pack_propagate(False)

        if parameter:
            tk.Label(
                frame,
                text=block.label or block.icon,
                bg=block.color,
                font=("Segoe UI", 18, "bold"),
            ).pack(expand=True)
        else:
            tk.Label(
                frame,
                text=block.icon,
                bg=block.color,
                font=("Segoe UI Symbol", 22, "bold"),
            ).pack(pady=(5, 0))
            tk.Label(
                frame,
                text=block.label,
                bg=block.color,
                font=("Segoe UI", 10, "bold"),
            ).pack()

        self.drag_ghost = ghost
        self.drag_offset_x = width // 2
        self.drag_offset_y = BLOCK_H // 2
        self.move_drag_ghost(x_root, y_root)

    def move_drag_ghost(self, x_root: int, y_root: int) -> None:
        if self.drag_ghost is None:
            return

        x = x_root - self.drag_offset_x
        y = y_root - self.drag_offset_y
        self.drag_ghost.geometry(f"+{x}+{y}")

    def destroy_drag_ghost(self) -> None:
        if self.drag_ghost is None:
            return
        self.drag_ghost.destroy()
        self.drag_ghost = None

    @staticmethod
    def first_tag_with_prefix(
        tags: tuple[str, ...],
        prefix: str,
    ) -> Optional[str]:
        for tag in tags:
            if tag.startswith(prefix):
                return tag
        return None

    def on_palette_press(self, event: tk.Event) -> None:
        canvas_x = self.palette_canvas.canvasx(event.x)
        canvas_y = self.palette_canvas.canvasy(event.y)

        items = self.palette_canvas.find_overlapping(
            canvas_x, canvas_y, canvas_x, canvas_y
        )
        for item in reversed(items):
            tag = self.first_tag_with_prefix(
                self.palette_canvas.gettags(item),
                "palette:",
            )
            if tag is None:
                continue

            block_id = tag.split(":", 1)[1]
            self.drag_state = DragState("palette", block_id)
            self.start_drag_ghost(block_id, event.x_root, event.y_root)
            return

    def on_program_press(self, event: tk.Event) -> None:
        canvas_x = self.program_canvas.canvasx(event.x)
        canvas_y = self.program_canvas.canvasy(event.y)

        items = self.program_canvas.find_overlapping(
            canvas_x, canvas_y, canvas_x, canvas_y
        )

        for item in reversed(items):
            tags = self.program_canvas.gettags(item)

            param_tag = self.first_tag_with_prefix(tags, "param:")
            if param_tag is not None:
                _, row_text, param_text = param_tag.split(":")
                row_index = int(row_text)
                param_index = int(param_text)
                block_id = self.layout_rows[row_index].node.params[param_index]
                self.drag_state = DragState(
                    "param",
                    block_id,
                    row_index=row_index,
                    param_index=param_index,
                )
                self.start_drag_ghost(block_id, event.x_root, event.y_root)
                return

            main_tag = self.first_tag_with_prefix(tags, "main:")
            if main_tag is not None:
                row_index = int(main_tag.split(":", 1)[1])
                block_id = self.layout_rows[row_index].node.block_id
                self.drag_state = DragState(
                    "main",
                    block_id,
                    row_index=row_index,
                )
                self.start_drag_ghost(block_id, event.x_root, event.y_root)
                return

    def on_drag_motion(self, event: tk.Event) -> None:
        if self.drag_state is not None:
            self.move_drag_ghost(event.x_root, event.y_root)

    @staticmethod
    def point_inside(widget: tk.Widget, x_root: int, y_root: int) -> bool:
        left = widget.winfo_rootx()
        top = widget.winfo_rooty()
        right = left + widget.winfo_width()
        bottom = top + widget.winfo_height()
        return left <= x_root <= right and top <= y_root <= bottom

    def program_coordinates(
        self,
        x_root: int,
        y_root: int,
    ) -> tuple[float, float]:
        widget_x = x_root - self.program_canvas.winfo_rootx()
        widget_y = y_root - self.program_canvas.winfo_rooty()

        return (
            self.program_canvas.canvasx(widget_x),
            self.program_canvas.canvasy(widget_y),
        )

    def remove_dragged_existing_item(self) -> Optional[ProgramNode | str]:
        state = self.drag_state
        if state is None or state.source_type == "palette":
            return None
        if state.row_index is None:
            return None

        row = self.layout_rows[state.row_index]

        if state.source_type == "main":
            return row.parent.pop(row.index)

        if state.source_type == "param" and state.param_index is not None:
            return row.node.params.pop(state.param_index)

        return None

    @staticmethod
    def point_in_rect(
        x: float,
        y: float,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> bool:
        return x1 <= x <= x2 and y1 <= y <= y2

    def deepest_child_area(
        self,
        x: float,
        y: float,
    ) -> Optional[ChildArea]:
        matches = [
            area
            for area in self.child_areas
            if self.point_in_rect(
                x, y, area.x1, area.y1, area.x2, area.y2
            )
        ]
        if not matches:
            return None
        return max(matches, key=lambda area: area.depth)

    def insertion_zone_at(
        self,
        x: float,
        y: float,
    ) -> Optional[InsertionZone]:
        matches = [
            zone
            for zone in self.insertion_zones
            if self.point_in_rect(
                x, y, zone.x1, zone.y1, zone.x2, zone.y2
            )
        ]
        if not matches:
            return None

        # If zones overlap because of nested containers, prefer the deepest.
        return max(matches, key=lambda zone: zone.depth)

    def target_list_from_geometry(
        self,
        x: float,
        y: float,
    ) -> list[ProgramNode]:
        area = self.deepest_child_area(x, y)
        if area is not None:
            return area.container.children
        return self.program

    def insertion_index_in_list(
        self,
        nodes: list[ProgramNode],
        y: float,
    ) -> int:
        if not nodes:
            return 0

        rows = [
            self.layout_rows[self.row_by_node[id(node)]]
            for node in nodes
            if id(node) in self.row_by_node
        ]
        if not rows:
            return len(nodes)

        for row in rows:
            center = (row.y + row.visual_bottom) / 2
            if y < center:
                return row.index

        return len(nodes)


    def parameter_zone_at(
        self,
        x: float,
        y: float,
    ) -> tuple[str, int, Optional[int]] | None:
        """
        The complete marked area is an ordered parameter-expression area.

        Examples:
            [1][2][3] -> 123
            [3][>][4] -> 3>4

        Horizontal drop position determines where the new parameter is inserted.
        """
        for row_index, row in enumerate(self.layout_rows):
            block_x = PROGRAM_X + row.depth * INDENT
            param_x = block_x + BLOCK_W + 22

            x1 = param_x - 4
            y1 = row.y + 3
            x2 = param_x + PARAM_AREA_W
            y2 = row.y + BLOCK_H - 3

            if not self.point_in_rect(x, y, x1, y1, x2, y2):
                continue

            relative_x = x - param_x
            cell_width = PARAM_W + PALETTE_GAP

            insert_index = round(relative_x / cell_width)
            insert_index = max(
                0,
                min(insert_index, len(row.node.params)),
            )

            return ("paraminsert", row_index, insert_index)

        return None

    def exact_parameter_target(
        self,
        x: float,
        y: float,
    ) -> tuple[str, int, Optional[int]] | None:
        # The full expression area has priority. This allows insertion
        # before, between and after existing parameters.
        zone_target = self.parameter_zone_at(x, y)
        if zone_target is not None:
            return zone_target

        items = self.program_canvas.find_overlapping(x, y, x, y)

        for item in reversed(items):
            tags = self.program_canvas.gettags(item)

            main_tag = self.first_tag_with_prefix(tags, "main:")
            if main_tag is not None:
                return (
                    "main",
                    int(main_tag.split(":", 1)[1]),
                    None,
                )

        return None

    def insert_main_node(
        self,
        node: ProgramNode,
        x: float,
        y: float,
    ) -> None:
        if not self.program:
            self.program.append(node)
            return

        # 1. An explicit gap always wins. This makes "between blocks"
        # deterministic rather than based on the nearest row.
        zone = self.insertion_zone_at(x, y)
        if zone is not None:
            zone.parent.insert(
                max(0, min(zone.index, len(zone.parent))),
                node,
            )
            return

        # 2. Otherwise the real child rectangle determines nesting.
        target_list = self.target_list_from_geometry(x, y)

        # 3. Inside that list, vertical position decides before/after.
        index = self.insertion_index_in_list(target_list, y)
        target_list.insert(
            max(0, min(index, len(target_list))),
            node,
        )

    def insert_parameter(
        self,
        block_id: str,
        target: tuple[str, int, Optional[int]] | None,
    ) -> None:
        if target is None:
            return

        target_type, row_index, param_index = target
        node = self.layout_rows[row_index].node

        if target_type == "paraminsert":
            if param_index is None:
                param_index = len(node.params)

            node.params.insert(
                max(0, min(param_index, len(node.params))),
                block_id,
            )
            return

        if target_type in ("main", "paramzone"):
            node.params.append(block_id)
            return

        if target_type == "param" and param_index is not None:
            node.params.insert(param_index, block_id)

    def on_drag_release(self, event: tk.Event) -> None:
        state = self.drag_state
        if state is None:
            return

        # Existing items are deleted by dragging them back to BLOCKS.
        if self.point_inside(
            self.palette_canvas,
            event.x_root,
            event.y_root,
        ):
            if state.source_type != "palette":
                self.remove_dragged_existing_item()
                self.finish_drag(redraw=True)
            else:
                self.finish_drag(redraw=False)
            return

        if not self.point_inside(
            self.program_canvas,
            event.x_root,
            event.y_root,
        ):
            self.finish_drag(redraw=False)
            return

        x, y = self.program_coordinates(event.x_root, event.y_root)
        block = self.blocks[state.block_id]

        if block.kind == "parameter":
            target = self.exact_parameter_target(x, y)

            if state.source_type == "param":
                removed = self.remove_dragged_existing_item()
                if isinstance(removed, str):
                    self.build_layout()
                    target = self.exact_parameter_target(x, y)
                    self.insert_parameter(removed, target)
            elif state.source_type == "palette":
                self.insert_parameter(state.block_id, target)

            self.finish_drag(redraw=True)
            return

        if state.source_type == "main":
            removed = self.remove_dragged_existing_item()
            if not isinstance(removed, ProgramNode):
                self.finish_drag(redraw=False)
                return
            moving_node = removed
            # Geometry must be rebuilt after removal before finding a target.
            self.build_layout()
        else:
            moving_node = ProgramNode(block_id=state.block_id)

        self.insert_main_node(moving_node, x, y)
        self.finish_drag(redraw=True)

    def finish_drag(self, redraw: bool) -> None:
        self.drag_state = None
        self.destroy_drag_ghost()
        if redraw:
            self.redraw_all()

    def parameter_values(self, node: ProgramNode) -> list[str]:
        values: list[str] = []
        for param_id in node.params:
            block = self.blocks[param_id]
            values.append(
                str(
                    block.value
                    if block.value is not None
                    else block.label
                )
            )
        return values

    def substitute_parameters(
        self,
        node: ProgramNode,
        template: str,
    ) -> str:
        block = self.blocks[node.block_id]
        values = self.parameter_values(node)

        result = template

        if values:
            expression = "".join(values)
        elif block.defaults:
            expression = str(block.defaults[0])
        else:
            expression = ""

        # New explicit syntax: {params} means the complete expression.
        result = result.replace("{params}", expression)

        # Compatibility with the current blocks.json:
        # if the template only has {p1}, treat it as the complete expression.
        # Thus existing "time.sleep({p1})" works with [1][2][3] -> 123
        # and existing "if {p1}:" works with [3][>][4] -> 3>4.
        has_later_positional = any(
            f"{{p{index}}}" in result
            for index in range(2, 10)
        )

        if "{p1}" in result and not has_later_positional:
            result = result.replace("{p1}", expression)
            return result

        # Preserve old positional behavior for templates that actually use
        # several separate placeholders such as {p1}, {p2}, ...
        count = max(len(values), len(block.defaults))

        for index in range(count):
            if index < len(values):
                value = values[index]
            elif index < len(block.defaults):
                value = str(block.defaults[index])
            else:
                value = ""

            result = result.replace(
                f"{{p{index + 1}}}",
                value,
            )

        return result

    def render_node(
        self,
        node: ProgramNode,
        indent: int = 0,
    ) -> list[str]:
        block = self.blocks[node.block_id]
        prefix = "    " * indent

        if block.kind == "container":
            header = self.substitute_parameters(
                node,
                block.header,
            )
            lines = [prefix + header]

            if node.children:
                for child in node.children:
                    lines.extend(
                        self.render_node(child, indent + 1)
                    )
            else:
                lines.append(prefix + "    pass")

            return lines

        code = self.substitute_parameters(node, block.code)
        return [
            prefix + line if line.strip() else line
            for line in code.splitlines()
        ]

    def collect_used_blocks(
        self,
        nodes: list[ProgramNode],
        used: set[str],
    ) -> None:
        for node in nodes:
            used.add(node.block_id)
            used.update(node.params)
            self.collect_used_blocks(node.children, used)

    def update_code(self) -> None:
        used: set[str] = set()
        self.collect_used_blocks(self.program, used)

        imports: list[str] = []
        for block_id in self.block_order:
            if block_id not in used:
                continue
            for import_line in self.blocks[block_id].imports:
                if import_line not in imports:
                    imports.append(import_line)

        code_lines: list[str] = []
        for node in self.program:
            code_lines.extend(self.render_node(node))

        generated = "\n".join(imports)
        if imports and code_lines:
            generated += "\n\n"
        generated += "\n".join(code_lines)

        self.code_text.delete("1.0", "end")
        self.code_text.insert("1.0", generated)

    def copy_code(self) -> None:
        text = self.code_text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_label.config(
            text="Python code copied to clipboard"
        )


    @staticmethod
    def program_node_to_dict(node: ProgramNode) -> dict[str, Any]:
        return {
            "block_id": node.block_id,
            "params": list(node.params),
            "children": [
                IconProgrammer.program_node_to_dict(child)
                for child in node.children
            ],
        }

    def program_node_from_dict(self, data: dict[str, Any]) -> ProgramNode:
        block_id = str(data["block_id"])

        if block_id not in self.blocks:
            raise ValueError(
                f"Unknown block '{block_id}' in saved visual program."
            )

        params = [str(value) for value in data.get("params", [])]

        for param_id in params:
            if param_id not in self.blocks:
                raise ValueError(
                    f"Unknown parameter block '{param_id}' "
                    f"in saved visual program."
                )

        children_raw = data.get("children", [])
        if not isinstance(children_raw, list):
            raise ValueError(
                f"'children' of block '{block_id}' must be a list."
            )

        children = [
            self.program_node_from_dict(child)
            for child in children_raw
        ]

        return ProgramNode(
            block_id=block_id,
            params=params,
            children=children,
        )

    def visual_program_to_dict(self) -> dict[str, Any]:
        return {
            "format": "mbot2-icon-program",
            "version": 1,
            "program": [
                self.program_node_to_dict(node)
                for node in self.program
            ],
        }

    def visual_program_from_dict(self, data: dict[str, Any]) -> None:
        if data.get("format") != "mbot2-icon-program":
            raise ValueError(
                "This file is not an mBot2 Icon Programmer project."
            )

        version = data.get("version")
        if version != 1:
            raise ValueError(
                f"Unsupported visual program version: {version}"
            )

        program_raw = data.get("program", [])
        if not isinstance(program_raw, list):
            raise ValueError("'program' must be a list.")

        self.program = [
            self.program_node_from_dict(node)
            for node in program_raw
        ]
        self.redraw_all()

    def save_visual_program_to_file(self, filename: str) -> None:
        path = Path(filename)
        with path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(
                self.visual_program_to_dict(),
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

        self.current_program_file = str(path)
        self.status_label.config(
            text=f"Saved visual program: {path.name}"
        )

    def save_visual_program(self) -> None:
        if self.current_program_file is None:
            self.save_visual_program_as()
            return

        try:
            self.save_visual_program_to_file(
                self.current_program_file
            )
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror(
                "Save visual program",
                str(exc),
            )

    def save_visual_program_as(self) -> None:
        filename = filedialog.asksaveasfilename(
            parent=self,
            title="Save visual program",
            defaultextension=".json",
            filetypes=[
                ("mBot2 visual program", "*.json"),
                ("All files", "*.*"),
            ],
        )

        if not filename:
            return

        try:
            self.save_visual_program_to_file(filename)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror(
                "Save visual program",
                str(exc),
            )

    def open_visual_program(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self,
            title="Open visual program",
            filetypes=[
                ("mBot2 visual program", "*.json"),
                ("All files", "*.*"),
            ],
        )

        if not filename:
            return

        try:
            path = Path(filename)
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)

            if not isinstance(data, dict):
                raise ValueError(
                    "Visual program file must contain a JSON object."
                )

            self.visual_program_from_dict(data)
            self.current_program_file = str(path)
            self.status_label.config(
                text=f"Opened visual program: {path.name}"
            )

        except (
            OSError,
            json.JSONDecodeError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            messagebox.showerror(
                "Open visual program",
                str(exc),
            )

    def clear_program(self) -> None:
        self.program.clear()
        self.redraw_all()

    COM_PORT = "COM7"

    def do_upload(self) -> None:
        result = upload_to_mbot2(
            self.code_text.get("1.0", "end-1c"),
            port=self.COM_PORT,  # or None for auto-detect
            status_callback=lambda s: self.status_label.config(text=s),
        )

        if result.ok:
            messagebox.showinfo("Upload", result.message)
        else:
            messagebox.showerror(
                "Upload",
                result.message + "\n\n" + result.stderr,
            )

    def do_test_connection(self) -> None:
        result = test_connection(
            port=self.COM_PORT,  # or None for auto-detect
        )

        if result.ok:
            messagebox.showinfo("Connection", result.message)
        else:
            messagebox.showerror(
                "Connection",
                result.message + "\n\n" + result.stderr,
            )

if __name__ == "__main__":
    IconProgrammer().mainloop()