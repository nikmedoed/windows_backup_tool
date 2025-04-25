import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext

from .config import Settings, PathRule
from .copier import run_backup
from src.ExcludeDialog import ExcludeDialog
from .scheduler import (
    schedule_daily   as daily,
    schedule_weekly  as weekly,
    schedule_onstart as onstart,
    schedule_onidle  as onidle,
    delete, _exists
)


# ---------- Main Application ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Backup Tool Settings")
        self.cfg = Settings.load() or Settings(target_dir="")
        self._build()

    def _build(self):
        # главный фрейм
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(7, weight=1)

        # Целевая директория
        ttk.Label(frm, text="Цель копии:").grid(row=0, column=0, sticky="w")
        self.ent_target = ttk.Entry(frm, width=50)
        self.ent_target.grid(row=0, column=1, sticky="we")
        ttk.Button(frm, text="…", width=3, command=self._pick_target).grid(row=0, column=2, padx=5)

        # Источники
        ttk.Label(frm, text="Источники:").grid(row=1, column=0, sticky="nw", pady=(10,0))
        self.lst_src = tk.Listbox(frm, height=4)
        self.lst_src.grid(row=1, column=1, sticky="we")
        self.lst_src.bind("<<ListboxSelect>>", lambda e: self._refresh_excl())
        btns_src = ttk.Frame(frm)
        btns_src.grid(row=1, column=2, sticky="n", pady=(10,0))
        ttk.Button(btns_src, text="+", command=self._add_src, width=3).pack(pady=2)
        ttk.Button(btns_src, text="–", command=self._del_src, width=3).pack(pady=2)
        ttk.Button(btns_src, text="Clr", command=self._clr_src, width=3).pack(pady=2)

        # Исключения
        ttk.Label(frm, text="Исключения:").grid(row=2, column=0, sticky="nw", pady=(10,0))
        self.lst_excl = tk.Listbox(frm, height=4)
        self.lst_excl.grid(row=2, column=1, sticky="we")
        ttk.Button(frm, text="Исключать", command=self._edit_excl).grid(row=2, column=2, sticky="n", pady=(10,0))

        # Планировщик
        sched = ttk.Labelframe(frm, text="Периодичность")
        sched.grid(row=3, column=0, columnspan=3, pady=(15,0), sticky="we")
        self.var_day   = tk.BooleanVar()
        self.var_week  = tk.BooleanVar()
        self.var_start = tk.BooleanVar()
        self.var_idle  = tk.BooleanVar()
        for i,(var,txt) in enumerate([
            (self.var_day,   "Ежедневно 03:00"),
            (self.var_week,  "Еженедельно (Пн 03:00)"),
            (self.var_start,"При включении системы"),
            (self.var_idle,  "При пробуждении/простое")
        ]):
            ttk.Checkbutton(sched, text=txt, variable=var).grid(row=i, column=0, sticky="w", pady=2)

        # Кнопки
        btnbar = ttk.Frame(frm)
        btnbar.grid(row=5, column=0, columnspan=3, pady=(20,0), sticky="we")
        ttk.Button(btnbar, text="Сохранить",   command=self._save).pack(side="left")
        ttk.Button(btnbar, text="Восстановить",command=self._restore).pack(side="left", padx=5)
        ttk.Button(btnbar, text="Сделать копию",command=self._run).pack(side="left", padx=5)
        ttk.Button(btnbar, text="Выход",       command=self.destroy).pack(side="right")

        # Статус
        self.lbl_status = ttk.Label(frm, text="", foreground="green")
        self.lbl_status.grid(row=6, column=0, columnspan=3, sticky="w", pady=5)

        # Progress + Log
        self.pb = ttk.Progressbar(frm, maximum=100)
        self.pb.grid(row=7, column=0, columnspan=3, sticky="we", pady=(0,5))
        self.txt_log = scrolledtext.ScrolledText(frm, height=6, state="disabled")
        self.txt_log.grid(row=8, column=0, columnspan=3, sticky="nsew")

        # заполнить из настроек
        self._load()

    # --- вспомогательные методы (pick/add/del/refresh) ---
    def _pick_target(self):
        d = filedialog.askdirectory(title="Куда копировать")
        if d:
            self.ent_target.delete(0, tk.END)
            self.ent_target.insert(0, d)

    def _add_src(self):
        d = filedialog.askdirectory(title="Добавить источник")
        if d:
            self.cfg.sources.append(PathRule(source=d))
            self._refresh_src()

    def _del_src(self):
        sel = self.lst_src.curselection()
        if sel:
            self.cfg.sources.pop(sel[0])
            self._refresh_src()

    def _clr_src(self):
        self.cfg.sources.clear()
        self._refresh_src()

    def _refresh_src(self):
        self.lst_src.delete(0, tk.END)
        for r in self.cfg.sources:
            self.lst_src.insert(tk.END, r.source)
        self._refresh_excl()

    def _refresh_excl(self):
        self.lst_excl.delete(0, tk.END)
        sel = self.lst_src.curselection()
        if not sel:
            return
        for e in self.cfg.sources[sel[0]].excludes:
            self.lst_excl.insert(tk.END, e)

    def _edit_excl(self):
        ExcludeDialog(self, self.cfg)
        self._refresh_excl()

    # --- действия: save / restore / run ---
    def _save(self, show_msg=True):
        tgt = self.ent_target.get().strip()
        if not tgt:
            self.lbl_status.config(text="Укажите целевую директорию", foreground="red")
            return
        self.cfg.target_dir = tgt
        self.cfg.save()

        if _exists():
            delete()
        if self.var_day.get():
            daily()
        if self.var_week.get():
            weekly()
        if self.var_start.get():
            onstart()
        if self.var_idle.get():
            onidle()

        self.lbl_status.config(text="Сохранено", foreground="green")

    def _restore(self):
        loaded = Settings.load()
        if not loaded:
            self.lbl_status.config(text="Настроек нет", foreground="red")
            return
        self.cfg = loaded
        self._load()
        self.lbl_status.config(text="Восстановлено", foreground="green")

    def _run(self):
        self._save(show_msg=False)
        self.lbl_status.config(text="Копирование…", foreground="orange")
        self.attributes("-disabled", True)

        q = queue.Queue()
        def prog(i, tot):  q.put(("prog", i, tot))
        def logm(m):       q.put(("log", m))

        threading.Thread(target=run_backup, args=(self.cfg, prog, logm), daemon=True).start()
        self.after(100, lambda: self._update_gui(q))

    def _update_gui(self, q: queue.Queue):
        while not q.empty():
            typ, *data = q.get()
            if typ == "prog":
                i, tot = data
                self.pb["value"] = i / tot * 100
            else:
                m = data[0]
                self.txt_log.config(state="normal")
                self.txt_log.insert("end", m + "\n")
                self.txt_log.see("end")
                self.txt_log.config(state="disabled")

        if self.pb["value"] < 100:
            self.after(200, lambda: self._update_gui(q))
        else:
            self.attributes("-disabled", False)
            self.lbl_status.config(text="Готово", foreground="green")

    def _load(self):
        self.ent_target.delete(0, tk.END)
        self.ent_target.insert(0, self.cfg.target_dir)
        self._refresh_src()


def open_gui():
    app = App()               # создаём только один App
    app.geometry("840x560")   # применяем к нему размер
    app.mainloop()            # запускаем цикл обработки
