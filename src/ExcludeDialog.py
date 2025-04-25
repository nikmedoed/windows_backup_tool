import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from src.config import Settings


class CheckTree(ttk.Treeview):
    CHECK, UNCHECK = "☑", "☐"

    def __init__(self, master, **kw):
        super().__init__(master, show="tree", **kw)
        self.node_map: dict[str, str] = {}  # path → iid
        self.tag_configure("unchecked", foreground="gray")
        self.bind("<Button-1>", self._on_click, True)

    def insert(self, parent, index, iid=None, checked=True, **kw):
        """
        При добавлении указываем checked=True (чек) или False (анчек).
        Сохраняем mapping path→iid.
        """
        path = kw.get("values", [""])[0]
        mark = self.CHECK if checked else self.UNCHECK
        text = kw.pop("text")
        iw = super().insert(parent, index, iid, text=f"{mark} {text}", values=[path])
        if path:
            self.node_map[path] = iw
        return iw

    def _on_click(self, ev):
        iid = self.identify_row(ev.y)
        x = ev.x
        # допустим, чек-бокс в первых 20px
        if not iid or x > 20:
            return
        self._toggle_recursive(iid)

    def _toggle_recursive(self, iid: str):
        txt = self.item(iid, "text")
        mark, name = txt[0], txt[2:]
        new_mark = self.UNCHECK if mark == self.CHECK else self.CHECK
        self.item(iid, text=f"{new_mark} {name}")
        # рекурсивно для детей
        for ch in self.get_children(iid):
            self._toggle_recursive(ch)

    def expand_all(self, iid=""):
        for ch in self.get_children(iid):
            self.item(ch, open=True)
            self.expand_all(ch)

    def collapse_all(self, iid=""):
        for ch in self.get_children(iid):
            self.item(ch, open=False)
            self.collapse_all(ch)


class ExcludeDialog(tk.Toplevel):
    def __init__(self, master, cfg: Settings):
        super().__init__(master)
        self.cfg = cfg
        self.title("Исключения")
        self.geometry("700x500")
        self.transient(master)
        self.grab_set()

        # верхняя панель кнопок
        # верхняя панель кнопок + легенда
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=5)

        # легенда в баре
        legend = ttk.Label(
            bar,
            text="☑ – включено (копируется),    ☐ – исключено (не копируется)",
            foreground="blue"
        )
        legend.pack(side="left", padx=(0, 20))

        # кнопки управления
        ttk.Button(bar, text="Развернуть всё", command=self._expand).pack(side="left")
        ttk.Button(bar, text="Свернуть всё", command=self._collapse).pack(side="left", padx=5)
        ttk.Button(bar, text="Сохранить", command=self._save).pack(side="right")

        # само дерево
        self.tree = CheckTree(self)
        self.tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # строим полный дерево по каждому source
        for rule in self.cfg.sources:
            root_id = self.tree.insert(
                "", "end",
                text=os.path.basename(rule.source) or rule.source,
                values=[rule.source],
                checked=True
            )
            self._add_subitems(root_id, Path(rule.source))

        # отмечаем уже сохранённые исключения как UNCHECK
        for rule in self.cfg.sources:
            for excl in rule.excludes:
                abs_path = str(Path(rule.source) / excl)
                iid = self.tree.node_map.get(abs_path)
                if iid:
                    self.tree._toggle_recursive(iid)

    def _add_subitems(self, parent_iid: str, path: Path):
        """Рекурсивно добавляем все файлы/папки под узлом parent_iid."""
        try:
            for entry in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                iid = self.tree.insert(
                    parent_iid, "end",
                    text=entry.name,
                    values=[str(entry)],
                    checked=True
                )
                if entry.is_dir():
                    self._add_subitems(iid, entry)
        except PermissionError:
            pass

    def _expand(self):
        self.tree.expand_all()

    def _collapse(self):
        self.tree.collapse_all()

    def _save(self):
        # очистим старые excludes
        for rule in self.cfg.sources:
            rule.excludes.clear()

        # пройдём по всем узлам, найдём UNCHECKED и сохраним относительный путь
        for rule in self.cfg.sources:
            src = Path(rule.source)
            for path_str, iid in self.tree.node_map.items():
                mark = self.tree.item(iid, "text")[0]
                if mark == CheckTree.UNCHECK and path_str.startswith(str(src)):
                    rel = Path(path_str).relative_to(src)
                    rule.excludes.append(str(rel).replace("\\", "/"))

        self.destroy()
