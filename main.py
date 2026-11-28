# main.py
"""Gravey — калькулятор устойчивости откосов. GUI (tkinter).

Этап 1: архитектура (geometry / layers / history / project), рабочие расчёты.
Этап 2: фильтрация — анизотропия, строгие ГУ, гидродинамическая сетка, расходы.
"""
import csv
import math
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from config import (APP_NAME, APP_VERSION, COLORS, CANVAS_WIDTH, CANVAS_HEIGHT,
                    DEFAULT_SCALE, MIN_SCALE, MAX_SCALE, GRID_STEP_M, ZOOM_FACTOR,
                    SCALE_PRESETS, SOIL_PRESETS, OBJ_TYPE_LABELS, STABILITY_METHODS,
                    IMPLEMENTED_METHODS, HELP_TEXT, get_soil_by_params,
                    BC_TYPES, METHOD_INFO_FILTRATION, FILTRATION_SOLVER_SETTINGS)
from geometry import distance_to_segment, arc_inside_polygon
from layers import SoilLayer, LayerCollection
from history import HistoryManager
from project import Project
from calculator import SlopeCalculator
from filtration import FiltrationCalculator, BoundaryCondition
from thermal import ThermalCalculator


# ====================================================================== ХОЛСТ
class SlopeCanvas(tk.Canvas):
    def __init__(self, parent, app, editable=True, **kw):
        super().__init__(parent, bg=COLORS["canvas_bg"],
                         width=CANVAS_WIDTH, height=CANVAS_HEIGHT,
                         highlightthickness=1, highlightbackground="#bbbbbb", **kw)
        self.app = app
        self.editable = editable
        self.scale = DEFAULT_SCALE
        self.offset_x = 100.0
        self.offset_y = 500.0

        self.drag_idx = None
        self.drag_water_idx = None
        self.pan_start = None
        self.bc_mode = False              # режим назначения граничных условий
        self.overlay = {}                 # результаты расчётов для отрисовки

        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Motion>", self.on_mouse_move)
        self.bind("<MouseWheel>", self.on_zoom)
        self.bind("<Button-4>", self.on_zoom)
        self.bind("<Button-5>", self.on_zoom)
        self.bind("<ButtonPress-2>", self.on_pan_start)
        self.bind("<B2-Motion>", self.on_pan_move)
        self.bind("<Shift-ButtonPress-1>", self.on_pan_start)
        self.bind("<Shift-B1-Motion>", self.on_pan_move)
        if editable:
            self.bind("<Button-1>", self.on_left_click)
            self.bind("<Button-3>", self.on_right_click)
            self.bind("<B1-Motion>", self.on_left_drag)
            self.bind("<ButtonRelease-1>", self.on_release)
            self.bind("<Double-Button-1>", self.on_double_click)

    # ---------------------------------------------------- модель <-> экран
    def model_to_screen(self, x, y):
        return self.offset_x + x * self.scale, self.offset_y - y * self.scale

    def screen_to_model(self, sx, sy):
        return (sx - self.offset_x) / self.scale, (self.offset_y - sy) / self.scale

    @property
    def project(self):
        return self.app.project

    # ------------------------------------------------------------ навигация
    def on_zoom(self, event):
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            f = ZOOM_FACTOR
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            f = 1 / ZOOM_FACTOR
        else:
            return
        new = max(MIN_SCALE, min(MAX_SCALE, self.scale * f))
        mx, my = self.screen_to_model(event.x, event.y)
        self.scale = new
        self.offset_x = event.x - mx * self.scale
        self.offset_y = event.y + my * self.scale
        self.redraw()
        self.app.update_status_scale()

    def on_pan_start(self, event):
        self.pan_start = (event.x, event.y, self.offset_x, self.offset_y)

    def on_pan_move(self, event):
        if self.pan_start:
            x0, y0, ox, oy = self.pan_start
            self.offset_x = ox + (event.x - x0)
            self.offset_y = oy + (event.y - y0)
            self.redraw()

    def reset_scale(self):
        self.scale = DEFAULT_SCALE
        self.offset_x, self.offset_y = 100.0, 500.0
        self.redraw()
        self.app.update_status_scale()

    def set_scale_preset(self, label):
        self.scale = SCALE_PRESETS.get(label, DEFAULT_SCALE)
        self.redraw()
        self.app.update_status_scale()

    def fit_view(self):
        pts = self.project.points
        if len(pts) < 2:
            self.reset_scale()
            return
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(self.winfo_width(), 300)
        h = max(self.winfo_height(), 300)
        span_x = max(max(xs) - min(xs), 1.0)
        span_y = max(max(ys) - min(ys), 1.0)
        self.scale = max(MIN_SCALE, min(MAX_SCALE,
                                        min(0.8 * w / span_x, 0.8 * h / span_y)))
        self.offset_x = 0.1 * w - min(xs) * self.scale
        self.offset_y = 0.9 * h + min(ys) * self.scale
        self.redraw()
        self.app.update_status_scale()

    # ------------------------------------------------------------ отрисовка
    def redraw(self):
        self.delete("all")
        self.draw_grid()
        self.draw_axes()
        self.draw_layers()
        self.draw_slope()
        self.draw_water()
        self.draw_overlay()

    draw = redraw  # совместимость со старыми вызовами

    def draw_grid(self):
        w = max(self.winfo_width(), CANVAS_WIDTH)
        h = max(self.winfo_height(), CANVAS_HEIGHT)
        step_m = GRID_STEP_M
        while step_m * self.scale < 12:
            step_m *= 5
        while step_m * self.scale > 120:
            step_m /= 2
        step = step_m * self.scale
        x0 = self.offset_x % step
        while x0 < w:
            self.create_line(x0, 0, x0, h, fill=COLORS["grid"])
            x0 += step
        y0 = self.offset_y % step
        while y0 < h:
            self.create_line(0, y0, w, y0, fill=COLORS["grid"])
            y0 += step
        self.create_text(w - 70, h - 14, text=f"сетка {step_m:g} м", fill="#888888")

    def draw_axes(self):
        w = max(self.winfo_width(), CANVAS_WIDTH)
        h = max(self.winfo_height(), CANVAS_HEIGHT)
        self.create_line(0, self.offset_y, w - 5, self.offset_y, arrow=tk.LAST, width=2)
        self.create_line(self.offset_x, h, self.offset_x, 5, arrow=tk.LAST, width=2)
        self.create_text(w - 25, self.offset_y + 14, text="X, м")
        self.create_text(self.offset_x - 18, 16, text="Y, м")

    def draw_slope(self):
        pts = self.project.points
        if len(pts) > 1:
            sp = [self.model_to_screen(x, y) for x, y in pts]
            if self.project.is_closed and len(pts) > 2:
                self.create_polygon(sp, outline=COLORS["slope"],
                                    fill=self.app.soil_color, stipple="gray25", width=2)
            else:
                self.create_line([c for p in sp for c in p],
                                 fill=COLORS["slope"], width=2)
        if self.editable:
            for i, (x, y) in enumerate(pts):
                sx, sy = self.model_to_screen(x, y)
                self.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, fill="red", outline="")
                self.create_text(sx + 10, sy - 10, text=str(i + 1),
                                 fill="red", font=("Arial", 8))

    def draw_layers(self):
        for l in self.project.layers:
            if len(l.polygon) < 3:
                continue
            sp = [self.model_to_screen(x, y) for x, y in l.polygon]
            fill = l.color if l.enabled else "#dddddd"
            self.create_polygon(sp, outline=COLORS["layer_line"], fill=fill,
                                stipple="gray50" if l.enabled else "gray12", width=2)
            cx, cy = l.centroid
            scx, scy = self.model_to_screen(cx, cy)
            self.create_text(scx, scy, text=f"{l.number}. {l.name}",
                             font=("Arial", 9, "bold"))
        cur = self.app.current_layer_pts
        if cur:
            sp = [self.model_to_screen(x, y) for x, y in cur]
            if len(sp) > 1:
                self.create_line([c for p in sp for c in p],
                                 fill=self.app.current_layer_color, width=2, dash=(4, 2))
            for x, y in cur:
                sx, sy = self.model_to_screen(x, y)
                self.create_oval(sx - 3, sy - 3, sx + 3, sy + 3, fill="orange", outline="")

    def draw_water(self):
        wp = self.project.water_points
        if len(wp) > 1:
            sp = [self.model_to_screen(x, y) for x, y in wp]
            self.create_line([c for p in sp for c in p], fill=COLORS["water"],
                             width=2, dash=(6, 3))
        for x, y in wp:
            sx, sy = self.model_to_screen(x, y)
            self.create_oval(sx - 3, sy - 3, sx + 3, sy + 3,
                             fill=COLORS["water"], outline="")

    def draw_overlay(self):
        for kind, data in self.overlay.items():
            if kind == "slip":
                self._draw_slip(data)
            elif kind == "curve":
                pts, color, width, dash = data
                if len(pts) > 1:
                    sp = [self.model_to_screen(x, y) for x, y in pts]
                    self.create_line([c for p in sp for c in p], fill=color,
                                     width=width, dash=dash)
            elif kind == "vectors":
                centers, vx, vy, _ = data
                for (cx, cy), dx, dy in zip(centers, vx, vy):
                    sx, sy = self.model_to_screen(cx, cy)
                    self.create_line(sx, sy, sx + dx * 40, sy - dy * 40,
                                     fill=COLORS["velocity"], arrow=tk.LAST)
            elif kind == "segments":
                for (p1, p2, color, width) in data:
                    a = self.model_to_screen(*p1)
                    b = self.model_to_screen(*p2)
                    self.create_line(a[0], a[1], b[0], b[1], fill=color, width=width)
            elif kind == "polylines":
                for pts, color, width in data:
                    if len(pts) > 1:
                        sp = [self.model_to_screen(x, y) for x, y in pts]
                        self.create_line([c for p in sp for c in p], fill=color, width=width)
            elif kind == "bc":
                for (p1, p2, color, label) in data:
                    a = self.model_to_screen(*p1)
                    b = self.model_to_screen(*p2)
                    self.create_line(a[0], a[1], b[0], b[1], fill=color, width=5)
                    self.create_text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 12,
                                     text=label, fill=color, font=("Arial", 8, "bold"))
            elif kind == "labels":
                for (x, y, text, color) in data:
                    sx, sy = self.model_to_screen(x, y)
                    self.create_text(sx, sy, text=text, fill=color, font=("Arial", 8))

    def _draw_slip(self, circles):
        calc = self.app.calculator
        if calc is None:
            return
        for idx, (fs, cx, cy, r) in enumerate(circles):
            color = COLORS["slip_circle"] if idx == 0 else COLORS["slip_other"]
            width = 3 if idx == 0 else 1
            for seg in arc_inside_polygon(cx, cy, r, calc.geom):
                sp = [self.model_to_screen(x, y) for x, y in seg]
                self.create_line([c for p in sp for c in p], fill=color, width=width)
            if idx == 0:
                sx, sy = self.model_to_screen(cx, cy)
                self.create_oval(sx - 3, sy - 3, sx + 3, sy + 3, fill=color, outline="")
                self.create_text(sx + 8, sy, anchor=tk.W, fill=color,
                                 text=f"FS={fs:.3f}", font=("Arial", 9, "bold"))

    def draw_custom_curve(self, curve_data, color="blue", width=2, dash=None):
        """Список точек либо кортеж МКЭ (centers, vx, vy, speeds)."""
        if curve_data is None:
            return
        if isinstance(curve_data, tuple) and len(curve_data) == 4:
            self.overlay["vectors"] = curve_data
        else:
            self.overlay["curve"] = (list(curve_data), color, width, dash)
        self.redraw()

    # -------------------------------------------------------- редактирование
    def snapshot(self, label=""):
        self.app.history.push(self.project.get_state(), label)

    def on_left_click(self, event):
        if self.pan_start:
            return
        mx, my = self.screen_to_model(event.x, event.y)

        # режим назначения граничных условий (вкладка «Фильтрация»)
        if self.bc_mode:
            self.app.pick_boundary_segment(mx, my)
            return

        pr = self.project
        if self.app.layer_mode:
            self.app.current_layer_pts.append((mx, my))
            self.redraw()
            return

        if not self.app.water_mode and len(pr.points) > 2:
            fx, fy = pr.points[0]
            if math.hypot(mx - fx, my - fy) * self.scale < 12:
                self.snapshot("замыкание контура")
                pr.is_closed = True
                self.redraw()
                return

        target = pr.water_points if self.app.water_mode else pr.points
        for i, (px, py) in enumerate(target):
            if math.hypot(px - mx, py - my) * self.scale < 10:
                self.snapshot("перемещение точки")
                if self.app.water_mode:
                    self.drag_water_idx = i
                else:
                    self.drag_idx = i
                return

        if not self.app.water_mode and len(pr.points) > 1:
            n = len(pr.points)
            rng = range(n) if pr.is_closed else range(n - 1)
            for i in rng:
                p1, p2 = pr.points[i], pr.points[(i + 1) % n]
                if distance_to_segment((mx, my), p1, p2) * self.scale < 8:
                    self.snapshot("разбиение отрезка")
                    pr.points.insert(i + 1, (mx, my))
                    self.redraw()
                    return

        self.snapshot("добавление точки")
        if self.app.water_mode:
            pr.water_points.append((mx, my))
            pr.water_points.sort(key=lambda p: p[0])
        else:
            pr.points.append((mx, my))
            pr.is_closed = False
        self.redraw()

    def on_double_click(self, event):
        mx, my = self.screen_to_model(event.x, event.y)
        idx = self.project.layers.hit_index((mx, my))
        if idx is not None:
            self.app.edit_layer_dialog(idx)

    def on_right_click(self, event):
        mx, my = self.screen_to_model(event.x, event.y)
        pr = self.project
        for lst in (pr.water_points, pr.points):
            for i, (px, py) in enumerate(lst):
                if math.hypot(px - mx, py - my) * self.scale < 10:
                    self.snapshot("удаление точки")
                    del lst[i]
                    if lst is pr.points and len(pr.points) < 3:
                        pr.is_closed = False
                    self.redraw()
                    return

    def on_left_drag(self, event):
        if self.drag_idx is None and self.drag_water_idx is None:
            return
        mx, my = self.screen_to_model(event.x, event.y)
        if self.drag_idx is not None:
            self.project.points[self.drag_idx] = (mx, my)
        else:
            self.project.water_points[self.drag_water_idx] = (mx, my)
        self.redraw()

    def on_release(self, event):
        self.drag_idx = self.drag_water_idx = None
        self.pan_start = None

    def on_mouse_move(self, event):
        mx, my = self.screen_to_model(event.x, event.y)
        self.app.update_status_coords(mx, my)


# =========================================================== ОКНО СВОЙСТВ СЛОЯ
class LayerDialog(tk.Toplevel):
    FIELDS = [
        ("name", "Название грунта", str),
        ("gamma", "γ, кН/м³", float),
        ("gamma_sat", "γ_sat, кН/м³ (0=авто)", float),
        ("c", "c сухое, кПа", float),
        ("c_wet", "c мокрое, кПа (0=авто)", float),
        ("phi", "φ, град", float),
        ("k_c", "k_c (снижение c)", float),
        ("k_phi", "k_phi (снижение φ)", float),
        ("h_cap", "h капиллярного подъёма, м", float),
        ("c_h_factor", "Анизотропия c_h/c_v", float),
        ("k_x", "k_x, м/сут", float),
        ("k_z", "k_z, м/сут", float),
        ("anisotropy_angle", "Угол залегания α, град", float),
        ("E", "Модуль деформации E, кПа", float),
        ("nu", "Коэф. Пуассона ν", float),
        ("porosity", "Пористость n", float),
        ("moisture", "Влажность W", float),
        ("rho_dry", "ρ сухого, т/м³", float),
        ("rho_sat", "ρ насыщенного, т/м³", float),
        ("placement_day", "День отсыпки (этап 4)", float),
        ("placement_temp", "Температура отсыпки, °C", float),
    ]

    def __init__(self, parent, layer):
        super().__init__(parent)
        self.title(f"Слой №{layer.number}: {layer.name}")
        self.layer = layer
        self.result = None
        self.transient(parent)
        self.grab_set()
        self.vars = {}
        self._entries = []

        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.BOTH, expand=True)

        # --- источник данных: справочник или лаборатория ---
        src = ttk.LabelFrame(top, text="Источник свойств", padding=5)
        src.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.use_preset_var = tk.BooleanVar(value=getattr(layer, "use_preset", False))
        ttk.Checkbutton(src, text="Брать из справочника (иначе — лабораторные данные)",
                        variable=self.use_preset_var,
                        command=self._toggle_entries).pack(anchor=tk.W)
        row = ttk.Frame(src)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="Грунт:").pack(side=tk.LEFT)
        self.preset_var = tk.StringVar(value=layer.name)
        ttk.Combobox(row, textvariable=self.preset_var, values=list(SOIL_PRESETS.keys()),
                     width=18, state="readonly").pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="Заполнить", command=self.apply_preset).pack(side=tk.LEFT)

        # --- поля ---
        for r, (key, label, typ) in enumerate(self.FIELDS, start=1):
            ttk.Label(top, text=label).grid(row=r, column=0, sticky="w", pady=1)
            var = tk.StringVar(value=str(getattr(layer, key)))
            self.vars[key] = (var, typ)
            ent = ttk.Entry(top, textvariable=var, width=16)
            ent.grid(row=r, column=1, padx=6, pady=1)
            self._entries.append(ent)

        self.enabled_var = tk.BooleanVar(value=layer.enabled)
        ttk.Checkbutton(top, text="Учитывать слой в расчёте",
                        variable=self.enabled_var).grid(row=len(self.FIELDS) + 1,
                                                        column=0, columnspan=2,
                                                        sticky="w", pady=6)
        btns = ttk.Frame(top)
        btns.grid(row=len(self.FIELDS) + 2, column=0, columnspan=2, sticky="ew")
        ttk.Button(btns, text="OK", command=self.on_ok).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(side=tk.RIGHT)

        self._toggle_entries()

    def _toggle_entries(self):
        """Справочник — поля только для чтения; лабораторные данные — редактируемые."""
        if self.use_preset_var.get():
            self.apply_preset()
        state = "disabled" if self.use_preset_var.get() else "normal"
        for e in self._entries:
            e.config(state=state)

    def apply_preset(self):
        name = self.preset_var.get()
        p = SOIL_PRESETS.get(name, {})
        for key, (var, typ) in self.vars.items():
            if key in p:
                var.set(str(p[key]))
        self.vars["name"][0].set(name)

    def on_ok(self):
        data = {}
        try:
            for key, (var, typ) in self.vars.items():
                data[key] = typ(var.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Ошибка", "Проверьте числовые поля", parent=self)
            return
        data["enabled"] = self.enabled_var.get()
        data["use_preset"] = self.use_preset_var.get()
        data["color"] = SOIL_PRESETS.get(data["name"], {}).get("color", self.layer.color)
        self.result = data
        self.destroy()


# ==================================================================== ПРИЛОЖЕНИЕ
class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION} — калькулятор устойчивости откосов")
        self.root.geometry("1400x860")

        self.project = Project()
        self.history = HistoryManager()
        self.calculator = None
        self.last_result = None
        self.filt_result = None
        self.custom_bc = []               # пользовательские ГУ на сегментах контура

        self.water_mode = False
        self.layer_mode = False
        self.current_layer_pts = []
        self.current_layer_color = "#FFB3BA"
        self.current_layer_preset = "Суглинок"
        self.soil_color = "#FFB3BA"

        self.status_coords = tk.StringVar(value="Координаты: --")
        self.status_scale = tk.StringVar(value="Масштаб: 100%")
        self.status_obj = tk.StringVar(value="Объект: контур")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.create_menu()
        self.create_stability_tab()
        self.create_filtration_tab()
        self.create_thermal_tab()
        self.create_statusbar()

        self.root.bind_all("<Control-z>", lambda e: self.undo())
        self.root.bind_all("<Control-y>", lambda e: self.redo())
        self.root.bind_all("<Control-s>", lambda e: self.save_project())
        self.root.bind_all("<F1>", lambda e: self.show_help())
        self.canvas.redraw()

    # ------------------------------------------------------------- меню/статус
    def create_menu(self):
        mb = tk.Menu(self.root)
        self.root.config(menu=mb)

        m = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Проект", menu=m)
        m.add_command(label="Новый проект", command=self.new_project)
        m.add_command(label="Открыть…", command=self.load_project)
        m.add_command(label="Сохранить\tCtrl+S", command=self.save_project)
        m.add_command(label="Сохранить как…", command=self.save_project_as)
        m.add_separator()
        m.add_command(label="Экспорт отчёта (.txt)", command=self.save_report)
        m.add_command(label="Экспорт таблицы срезов (.csv)", command=self.export_csv)
        m.add_command(label="Экспорт расходов фильтрации (.csv)", command=self.export_discharge_csv)
        m.add_separator()
        m.add_command(label="Выход", command=self.root.quit)

        e = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Правка", menu=e)
        e.add_command(label="Отменить\tCtrl+Z", command=self.undo)
        e.add_command(label="Повторить\tCtrl+Y", command=self.redo)

        v = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Вид", menu=v)
        v.add_command(label="Сбросить масштаб", command=lambda: self.active_canvas().reset_scale())
        v.add_command(label="Вписать в окно", command=lambda: self.active_canvas().fit_view())
        v.add_separator()
        for label in SCALE_PRESETS:
            v.add_command(label=f"Масштаб {label}",
                          command=lambda l=label: self.active_canvas().set_scale_preset(l))

        h = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Справка", menu=h)
        h.add_command(label="Инструкция\tF1", command=self.show_help)
        h.add_command(label="Методика расчёта фильтрации", command=self.show_method_info)
        h.add_command(label="О программе", command=lambda: messagebox.showinfo(
            "О программе", f"{APP_NAME} {APP_VERSION}\nДипломная работа, 09.03.02"))

    def create_statusbar(self):
        bar = ttk.Frame(self.root, relief=tk.SUNKEN)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        for var, w in ((self.status_coords, 42), (self.status_scale, 32), (self.status_obj, 40)):
            ttk.Label(bar, textvariable=var, width=w, anchor=tk.W).pack(side=tk.LEFT, padx=6)

    def update_status_coords(self, x, y):
        self.status_coords.set(f"Координаты: X={x:.2f} м, Y={y:.2f} м")

    def update_status_scale(self):
        c = self.active_canvas()
        pct = c.scale / DEFAULT_SCALE * 100
        self.status_scale.set(f"Масштаб: {pct:.0f}%  (1 м = {c.scale:.1f} px)")

    def active_canvas(self):
        i = self.notebook.index(self.notebook.select())
        return (self.canvas, self.filt_canvas, self.therm_canvas)[i]

    def _field(self, parent, label, default):
        ttk.Label(parent, text=label).pack(anchor=tk.W)
        e = ttk.Entry(parent)
        e.insert(0, default)
        e.pack(fill=tk.X, pady=2)
        return e

    # --------------------------------------------------------------- история
    def undo(self):
        st = self.history.undo(self.project.get_state())
        if st:
            self.project.set_state(st)
            self.refresh_all()

    def redo(self):
        st = self.history.redo(self.project.get_state())
        if st:
            self.project.set_state(st)
            self.refresh_all()

    def refresh_all(self):
        self.sync_entries_from_project()
        self.update_layer_list()
        self.update_soil_color()
        for c in (self.canvas, self.filt_canvas, self.therm_canvas):
            c.redraw()
        self.redraw_filtration()

    # ============================================================ ВКЛАДКА 1: FS
    def create_stability_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="1. Устойчивость откоса")

        left = ttk.Frame(tab, width=270)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left.pack_propagate(False)

        tools = ttk.LabelFrame(left, text="Инструменты", padding=5)
        tools.pack(fill=tk.X, pady=4)
        ttk.Button(tools, text="Очистить всё", command=self.clear_canvas).pack(fill=tk.X, pady=2)
        self.btn_water = ttk.Button(tools, text="Режим УГВ: выкл", command=self.toggle_water_mode)
        self.btn_water.pack(fill=tk.X, pady=2)
        row = ttk.Frame(tools)
        row.pack(fill=tk.X, pady=2)
        ttk.Button(row, text="Сброс масштаба",
                   command=lambda: self.canvas.reset_scale()).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(row, text="Вписать",
                   command=lambda: self.canvas.fit_view()).pack(side=tk.LEFT, expand=True, fill=tk.X)
        sc = ttk.Frame(tools)
        sc.pack(fill=tk.X, pady=2)
        ttk.Label(sc, text="Масштаб:").pack(side=tk.LEFT)
        self.scale_box = ttk.Combobox(sc, values=list(SCALE_PRESETS.keys()),
                                      width=8, state="readonly")
        self.scale_box.set("1:200")
        self.scale_box.pack(side=tk.LEFT, padx=4)
        self.scale_box.bind("<<ComboboxSelected>>",
                            lambda e: self.canvas.set_scale_preset(self.scale_box.get()))

        soil = ttk.LabelFrame(left, text="Основной грунт", padding=5)
        soil.pack(fill=tk.X, pady=4)
        self.ent_gamma = self._field(soil, "γ, кН/м³", "19.0")
        self.ent_c = self._field(soil, "c, кПа", "15.0")
        self.ent_phi = self._field(soil, "φ, град", "18.0")
        self.soil_color_box = tk.Label(soil, text="Суглинок", bg=self.soil_color,
                                       font=("Arial", 9, "bold"))
        self.soil_color_box.pack(fill=tk.X, pady=4)
        self.ent_c.bind("<KeyRelease>", self.update_soil_color)
        self.ent_phi.bind("<KeyRelease>", self.update_soil_color)
        self.cap_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(soil, text="Учитывать капиллярные силы",
                        variable=self.cap_var).pack(anchor=tk.W)

        lay = ttk.LabelFrame(left, text="Слои грунта", padding=5)
        lay.pack(fill=tk.X, pady=4)
        ttk.Button(lay, text="Нарисовать слой", command=self.start_layer_drawing).pack(fill=tk.X, pady=2)
        ttk.Button(lay, text="Завершить слой", command=self.finish_layer_drawing).pack(fill=tk.X, pady=2)
        self.layer_listbox = tk.Listbox(lay, height=4, font=("Arial", 9))
        self.layer_listbox.pack(fill=tk.X, pady=4)
        self.layer_listbox.bind("<Double-Button-1>",
                                lambda e: self.edit_layer_dialog(self.sel_layer()))
        r = ttk.Frame(lay)
        r.pack(fill=tk.X)
        ttk.Button(r, text="Свойства",
                   command=lambda: self.edit_layer_dialog(self.sel_layer())).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(r, text="Вкл/выкл", command=self.toggle_layer).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(lay, text="Удалить слой", command=self.delete_selected_layer).pack(fill=tk.X, pady=2)

        calc = ttk.LabelFrame(left, text="Расчёт", padding=5)
        calc.pack(fill=tk.X, pady=4)
        self.obj_type = tk.StringVar(value="permanent")
        for key, label in OBJ_TYPE_LABELS.items():
            ttk.Radiobutton(calc, text=label, variable=self.obj_type, value=key).pack(anchor=tk.W)
        ttk.Label(calc, text="Метод:").pack(anchor=tk.W, pady=(6, 0))
        self.method_var = tk.StringVar(value="bishop")
        self.method_box = ttk.Combobox(calc, state="readonly", width=24,
                                       values=[STABILITY_METHODS[m] for m in IMPLEMENTED_METHODS])
        self.method_box.set(STABILITY_METHODS["bishop"])
        self.method_box.pack(fill=tk.X)
        ttk.Label(calc, text="Число срезов:").pack(anchor=tk.W, pady=(6, 0))
        self.ent_slices = ttk.Entry(calc)
        self.ent_slices.insert(0, "30")
        self.ent_slices.pack(fill=tk.X)
        ttk.Button(calc, text="РАССЧИТАТЬ FS",
                   command=self.calculate_stability).pack(fill=tk.X, pady=8, ipady=8)

        right = ttk.Frame(tab, width=250)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
        right.pack_propagate(False)
        ttk.Label(right, text="Результаты", font=("Arial", 12, "bold")).pack(pady=5)
        self.result_fs = ttk.Label(right, text="FS = --", font=("Arial", 26, "bold"),
                                   foreground="gray")
        self.result_fs.pack(pady=8)
        self.result_status = ttk.Label(right, text="Нет данных", font=("Arial", 11),
                                       justify=tk.CENTER)
        self.result_status.pack()
        self.calc_info = ttk.Label(right, text="", font=("Arial", 9),
                                   foreground="gray", justify=tk.CENTER)
        self.calc_info.pack(pady=8)
        ttk.Button(right, text="Отчёт (.txt)", command=self.save_report).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Таблица срезов (.csv)", command=self.export_csv).pack(fill=tk.X, pady=3)

        self.canvas = SlopeCanvas(tab, self, editable=True)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

    # ==================================================== ВКЛАДКА 2: ФИЛЬТРАЦИЯ
    def create_filtration_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="2. Фильтрация")

        left = ttk.Frame(tab, width=290)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left.pack_propagate(False)

        ttk.Button(left, text="Скопировать контур",
                   command=self.copy_slope_geo).pack(fill=tk.X, pady=3)
        ttk.Button(left, text="Выровнять границы",
                   command=self.straighten_boundaries).pack(fill=tk.X, pady=3)

        box = ttk.LabelFrame(left, text="Граничные условия", padding=5)
        box.pack(fill=tk.X, pady=4)
        ttk.Label(box, text="Низ — непроницаемый (всегда)",
                  foreground="#555555").pack(anchor=tk.W)
        self.ent_left_head = self._field(box, "Напор слева, м ОТ НИЗА (пусто=УГВ)", "7.5")
        self.ent_right_head = self._field(box, "Напор справа, м ОТ НИЗА (пусто=нет)", "")
        self.right_perm = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="Правая грань проницаема (высачивание)",
                        variable=self.right_perm).pack(anchor=tk.W)
        self.btn_bc = ttk.Button(box, text="Границы на контуре: выкл",
                                 command=self.toggle_bc_mode)
        self.btn_bc.pack(fill=tk.X, pady=3)
        ttk.Button(box, text="Очистить ГУ контура",
                   command=self.clear_custom_bc).pack(fill=tk.X)

        mesh = ttk.LabelFrame(left, text="Сетка и итерации", padding=5)
        mesh.pack(fill=tk.X, pady=4)
        self.ent_ppm = self._field(mesh, "Плотность сетки, узлов/м", "2")
        self.ent_eps1 = self._field(mesh, "ε₁ по кривой депрессии, м", "0.02")
        self.ent_maxit = self._field(mesh, "Макс. итераций", "40")
        self.dep_iter = tk.BooleanVar(value=True)
        ttk.Checkbutton(mesh, text="Уточнять кривую депрессии итерационно",
                        variable=self.dep_iter).pack(anchor=tk.W)

        vis = ttk.LabelFrame(left, text="Гидродинамическая сетка", padding=5)
        vis.pack(fill=tk.X, pady=4)
        self.show_eq = tk.BooleanVar(value=True)
        self.show_sl = tk.BooleanVar(value=True)
        self.show_vec = tk.BooleanVar(value=False)
        self.show_dep = tk.BooleanVar(value=True)
        self.show_lbl = tk.BooleanVar(value=True)
        for var, text in ((self.show_dep, "Кривая депрессии"),
                          (self.show_eq, "Линии равных напоров"),
                          (self.show_sl, "Линии тока"),
                          (self.show_vec, "Векторы скоростей"),
                          (self.show_lbl, "Подписи напоров")):
            ttk.Checkbutton(vis, text=text, variable=var,
                            command=self.redraw_filtration).pack(anchor=tk.W)

        ttk.Button(left, text="РАССЧИТАТЬ ФИЛЬТРАЦИЮ",
                   command=self.calculate_filtration).pack(fill=tk.X, pady=8, ipady=8)
        row = ttk.Frame(left)
        row.pack(fill=tk.X)
        ttk.Button(row, text="Расходы (.csv)",
                   command=self.export_discharge_csv).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(row, text="Методика",
                   command=self.show_method_info).pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.filt_info = ttk.Label(left, text="", justify=tk.LEFT, font=("Arial", 9))
        self.filt_info.pack(fill=tk.X, pady=4)

        self.filt_canvas = SlopeCanvas(tab, self, editable=True)
        self.filt_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

    # ================================================== ВКЛАДКА 3: ПРОМЕРЗАНИЕ
    def create_thermal_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="3. Промерзание/Оттаивание")
        left = ttk.Frame(tab, width=250)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left.pack_propagate(False)
        ttk.Button(left, text="Скопировать контур",
                   command=self.copy_slope_geo).pack(fill=tk.X, pady=4)
        self.ent_t_air = self._field(left, "Температура воздуха, °C", "-20")
        self.ent_days = self._field(left, "Длительность, суток", "30")
        self.ent_t_init = self._field(left, "Начальная температура грунта, °C", "5")
        self.ent_dx = self._field(left, "Шаг сетки, м", "0.5")
        ttk.Button(left, text="РАССЧИТАТЬ",
                   command=self.calculate_thermal).pack(fill=tk.X, pady=10, ipady=8)
        self.therm_info = ttk.Label(left, text="", justify=tk.LEFT, font=("Arial", 9))
        self.therm_info.pack(fill=tk.X, pady=4)
        self.therm_canvas = SlopeCanvas(tab, self, editable=False)
        self.therm_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

    # ------------------------------------------------------------- слои (GUI)
    def sel_layer(self):
        s = self.layer_listbox.curselection()
        return s[0] if s else None

    def start_layer_drawing(self):
        names = list(SOIL_PRESETS.keys())
        dlg = tk.Toplevel(self.root)
        dlg.title("Новый слой")
        dlg.transient(self.root)
        dlg.grab_set()
        ttk.Label(dlg, text="Выберите грунт:").pack(padx=10, pady=6)
        var = tk.StringVar(value=names[2] if len(names) > 2 else names[0])
        ttk.Combobox(dlg, textvariable=var, values=names, state="readonly").pack(padx=10)

        def ok():
            self.current_layer_preset = var.get()
            self.current_layer_color = SOIL_PRESETS[var.get()]["color"]
            self.current_layer_pts = []
            self.layer_mode = True
            self.water_mode = False
            self.btn_water.config(text="Режим УГВ: выкл")
            self.status_obj.set("Объект: рисование слоя")
            dlg.destroy()
            messagebox.showinfo("Слой", "Кликайте вершины слоя, затем «Завершить слой».")

        ttk.Button(dlg, text="OK", command=ok).pack(pady=8)

    def finish_layer_drawing(self):
        if len(self.current_layer_pts) < 3:
            messagebox.showwarning("Слой", "Нужно минимум 3 точки.")
            return
        self.history.push(self.project.get_state(), "добавление слоя")
        layer = SoilLayer.from_preset(self.current_layer_preset,
                                      self.current_layer_pts.copy(),
                                      number=len(self.project.layers) + 1)
        self.project.layers.add(layer)
        self.current_layer_pts = []
        self.layer_mode = False
        self.status_obj.set("Объект: контур")
        self.update_layer_list()
        self.canvas.redraw()

    def update_layer_list(self):
        self.layer_listbox.delete(0, tk.END)
        for l in self.project.layers:
            mark = "☑" if l.enabled else "☐"
            src = "спр" if getattr(l, "use_preset", False) else "лаб"
            self.layer_listbox.insert(tk.END, f"{mark} {l.number}. {l.name} "
                                              f"[{src}] k_x={l.k_x:g}, k_z={l.k_z:g}")

    def edit_layer_dialog(self, idx):
        if idx is None:
            return
        layer = self.project.layers[idx]
        dlg = LayerDialog(self.root, layer)
        self.root.wait_window(dlg)
        if dlg.result:
            self.history.push(self.project.get_state(), "свойства слоя")
            for k, v in dlg.result.items():
                setattr(layer, k, v)
            self.update_layer_list()
            self.canvas.redraw()
            self.redraw_filtration()

    def toggle_layer(self):
        idx = self.sel_layer()
        if idx is None:
            return
        self.history.push(self.project.get_state(), "вкл/выкл слоя")
        self.project.layers.toggle(idx)
        self.update_layer_list()
        self.canvas.redraw()

    def delete_selected_layer(self):
        idx = self.sel_layer()
        if idx is None:
            return
        self.history.push(self.project.get_state(), "удаление слоя")
        self.project.layers.remove(idx)
        self.update_layer_list()
        self.canvas.redraw()

    def update_soil_color(self, event=None):
        try:
            c = float(self.ent_c.get().replace(",", "."))
            phi = float(self.ent_phi.get().replace(",", "."))
        except ValueError:
            return
        name = get_soil_by_params(c, phi)
        self.soil_color = SOIL_PRESETS[name]["color"]
        self.soil_color_box.config(text=f"Основной: {name}", bg=self.soil_color)
        self.canvas.redraw()

    # ------------------------------------------------------------------ прочее
    def toggle_water_mode(self):
        self.water_mode = not self.water_mode
        self.layer_mode = False
        self.btn_water.config(text=f"Режим УГВ: {'ВКЛ' if self.water_mode else 'выкл'}")
        self.status_obj.set("Объект: " + ("УГВ" if self.water_mode else "контур"))

    def clear_canvas(self):
        if not messagebox.askyesno("Очистить", "Удалить контур, слои и УГВ?"):
            return
        self.history.push(self.project.get_state(), "очистка")
        self.project.points = []
        self.project.water_points = []
        self.project.layers = LayerCollection()
        self.project.is_closed = False
        self.current_layer_pts = []
        self.custom_bc = []
        self.filt_result = None
        self.canvas.overlay.clear()
        self.filt_canvas.overlay.clear()
        self.therm_canvas.overlay.clear()
        self.refresh_all()
        self.result_fs.config(text="FS = --", foreground="gray")
        self.result_status.config(text="Нет данных", foreground="black")
        self.calc_info.config(text="")
        self.filt_info.config(text="")

    def sync_entries_from_project(self):
        s = self.project.soil
        for ent, key in ((self.ent_gamma, "gamma"), (self.ent_c, "c"), (self.ent_phi, "phi")):
            ent.delete(0, tk.END)
            ent.insert(0, str(s.get(key, "")))
        self.obj_type.set(self.project.obj_type)
        self.ent_t_air.delete(0, tk.END)
        self.ent_t_air.insert(0, str(self.project.thermal.get("t_air", -20)))
        self.ent_days.delete(0, tk.END)
        self.ent_days.insert(0, str(self.project.thermal.get("days", 30)))
        self.ent_t_init.delete(0, tk.END)
        self.ent_t_init.insert(0, str(self.project.thermal.get("t_init", 5)))

        fb = self.project.filtration_bc or {}
        self.ent_left_head.delete(0, tk.END)
        self.ent_left_head.insert(0, "" if fb.get("left_head") is None else str(fb["left_head"]))
        self.ent_right_head.delete(0, tk.END)
        self.ent_right_head.insert(0, "" if fb.get("right_head") is None else str(fb["right_head"]))
        self.right_perm.set(bool(fb.get("right_seepage", False)))
        self.custom_bc = [BoundaryCondition.from_dict(d) for d in fb.get("custom", [])]

    def read_soil(self):
        try:
            return {"gamma": float(self.ent_gamma.get().replace(",", ".")),
                    "c": float(self.ent_c.get().replace(",", ".")),
                    "phi": float(self.ent_phi.get().replace(",", "."))}
        except ValueError:
            messagebox.showerror("Ошибка", "Проверьте свойства грунта (числа).")
            return None

    def copy_slope_geo(self):
        i = self.notebook.index(self.notebook.select())
        target = self.filt_canvas if i == 1 else self.therm_canvas
        target.overlay.clear()
        target.fit_view()
        if i == 1:
            self.redraw_filtration()
        else:
            target.redraw()

    # ------------------------------------------- граничные условия на контуре
    def toggle_bc_mode(self):
        self.filt_canvas.bc_mode = not self.filt_canvas.bc_mode
        self.btn_bc.config(
            text=f"Границы на контуре: {'ВКЛ' if self.filt_canvas.bc_mode else 'выкл'}")
        self.status_obj.set("Объект: " + ("граничные условия" if self.filt_canvas.bc_mode else "контур"))
        if self.filt_canvas.bc_mode:
            messagebox.showinfo("Граничные условия",
                                "Кликайте по сегменту контура на холсте фильтрации,\n"
                                "чтобы назначить тип граничного условия.\n\n"
                                "Напор задаётся в метрах ОТ НИЖНЕЙ границы.")

    def clear_custom_bc(self):
        self.custom_bc = []
        self.redraw_filtration()

    def pick_boundary_segment(self, mx, my):
        pts = self.project.points
        if len(pts) < 3:
            return
        best, best_d = None, 1e9
        n = len(pts)
        for i in range(n):
            p1, p2 = pts[i], pts[(i + 1) % n]
            d = distance_to_segment((mx, my), p1, p2)
            if d < best_d:
                best_d, best = d, (p1, p2)
        if best is None or best_d * self.filt_canvas.scale > 20:
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Граничное условие на сегменте")
        dlg.transient(self.root)
        dlg.grab_set()
        ttk.Label(dlg, text=f"Сегмент: ({best[0][0]:.2f}; {best[0][1]:.2f}) → "
                            f"({best[1][0]:.2f}; {best[1][1]:.2f})").pack(padx=10, pady=6)
        kind = tk.StringVar(value="head")
        for k, label in BC_TYPES.items():
            ttk.Radiobutton(dlg, text=label, variable=kind, value=k).pack(anchor=tk.W, padx=14)
        fr = ttk.Frame(dlg)
        fr.pack(padx=10, pady=6)
        ttk.Label(fr, text="Напор, м от низа:").pack(side=tk.LEFT)
        val = ttk.Entry(fr, width=8)
        val.insert(0, self.ent_left_head.get() or "7.5")
        val.pack(side=tk.LEFT, padx=4)

        def ok():
            try:
                v = float(val.get().replace(",", "."))
            except ValueError:
                v = 0.0
            self.custom_bc = [bc for bc in self.custom_bc if bc.segment != best]
            if kind.get() != "impermeable":
                self.custom_bc.append(BoundaryCondition(kind=kind.get(), value=v,
                                                        segment=best,
                                                        label=BC_TYPES[kind.get()]))
            dlg.destroy()
            self.redraw_filtration()

        ttk.Button(dlg, text="Назначить", command=ok).pack(pady=8)

    def straighten_boundaries(self):
        """«Граница всегда ровная»: боковые грани строго вертикальны."""
        pts = self.project.points
        if len(pts) < 3:
            return
        xs = [p[0] for p in pts]
        xmin, xmax = min(xs), max(xs)
        tol = (xmax - xmin) * 0.03
        self.history.push(self.project.get_state(), "выравнивание границ")
        new = []
        for x, y in pts:
            if abs(x - xmin) <= tol:
                new.append((xmin, y))
            elif abs(x - xmax) <= tol:
                new.append((xmax, y))
            else:
                new.append((x, y))
        self.project.points = new
        self.custom_bc = []          # сегменты сместились — сбрасываем привязку ГУ
        self.refresh_all()
        messagebox.showinfo("Готово", "Боковые грани выровнены по вертикали.\n"
                                      "ГУ на сегментах контура сброшены — назначьте заново.")

    def show_method_info(self):
        win = tk.Toplevel(self.root)
        win.title("Методика расчёта фильтрации")
        win.geometry("780x660")
        txt = tk.Text(win, wrap=tk.WORD, font=("Consolas", 10))
        txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        txt.insert("1.0", METHOD_INFO_FILTRATION)
        txt.config(state=tk.DISABLED)
        ttk.Button(win, text="Закрыть", command=win.destroy).pack(pady=6)

    # ------------------------------------------------------------- расчёты
    def calculate_stability(self):
        if len(self.project.points) < 3:
            messagebox.showwarning("Внимание", "Нарисуйте контур откоса (≥3 точки).")
            return
        soil = self.read_soil()
        if soil is None:
            return
        self.project.soil = soil
        self.project.obj_type = self.obj_type.get()
        self.project.is_closed = True
        try:
            n = max(5, int(self.ent_slices.get()))
        except ValueError:
            n = 30

        self.calculator = SlopeCalculator(
            self.project.points, self.project.water_points,
            obj_type=self.project.obj_type, layers=self.project.layers,
            soil_props=soil, consider_capillary=self.cap_var.get())

        self.root.config(cursor="watch")
        self.root.update()
        try:
            results = self.calculator.search_critical("bishop", n_slices=n)
        finally:
            self.root.config(cursor="")

        if not results:
            messagebox.showinfo("Результат",
                                "Не найдено корректных поверхностей скольжения.\n"
                                "Проверьте форму контура.")
            return
        best = results[0]
        self.last_result = best
        req = best.required_fs
        color = "green" if best.stable else "red"
        self.result_fs.config(text=f"FS = {best.fs:.3f}", foreground=color)
        self.result_status.config(
            text=("УСТОЙЧИВО" if best.stable else "НЕ УСТОЙЧИВО") + f"\n(требуется ≥ {req})",
            foreground=color)
        self.calc_info.config(text=f"Метод: Бишоп (упрощ.)\n"
                                   f"Центр: ({best.cx:.2f}; {best.cy:.2f})\n"
                                   f"R = {best.r:.2f} м\n"
                                   f"Срезов: {len(best.slices)}, итераций: {best.iterations}")
        self.canvas.overlay["slip"] = [(r.fs, r.cx, r.cy, r.r) for r in results]
        self.canvas.redraw()
        self.project.results["stability"] = {"fs": best.fs, "required": req,
                                             "cx": best.cx, "cy": best.cy, "r": best.r}

    def calculate_filtration(self):
        if len(self.project.points) < 3:
            messagebox.showwarning("Внимание", "Нарисуйте контур на вкладке 1.")
            return
        try:
            ppm = float(self.ent_ppm.get().replace(",", "."))
            eps1 = float(self.ent_eps1.get().replace(",", "."))
            maxit = int(self.ent_maxit.get())
        except ValueError:
            ppm, eps1, maxit = 2.0, 0.02, 40

        bc = {"right_seepage": self.right_perm.get()}
        for key, ent in (("left_head", self.ent_left_head), ("right_head", self.ent_right_head)):
            t = ent.get().strip().replace(",", ".")
            bc[key] = float(t) if t else None

        settings = dict(FILTRATION_SOLVER_SETTINGS)
        settings.update({"points_per_meter": ppm, "eps_curve": eps1,
                         "max_iter": maxit, "depression_iterations": self.dep_iter.get()})

        # сохраняем ГУ в проект
        self.project.filtration_bc = dict(bc)
        self.project.filtration_bc["custom"] = [b.to_dict() for b in self.custom_bc]

        self.copy_slope_geo()
        calc = FiltrationCalculator(self.project.points, self.project.layers,
                                    points_per_meter=ppm, settings=settings)
        self.root.config(cursor="watch")
        self.root.update()
        try:
            res = calc.solve(water_points=self.project.water_points, bc=bc,
                             custom_bc=[b.to_dict() for b in self.custom_bc])
        except TypeError:
            self.root.config(cursor="")
            messagebox.showerror(
                "Версия модуля",
                "filtration.py устарел: нет поддержки settings/custom_bc.\n"
                "Замените файл версией этапа 2.")
            return
        finally:
            self.root.config(cursor="")

        if not res:
            messagebox.showerror("Ошибка расчёта", calc.error or "Неизвестная ошибка")
            return
        if not hasattr(res, "discharge"):
            messagebox.showerror("Версия модуля",
                                 "filtration.py вернул результат старого формата.\n"
                                 "Замените файл версией этапа 2.")
            return

        self.filt_result = res
        d = res.discharge
        self.filt_info.config(
            text=f"Узлов: {len(res.nodes)}, элементов: {len(res.elements)}\n"
                 f"Итераций: {res.iterations}\n"
                 f"Невязка кривой: {res.curve_residual:.4f} м\n"
                 f"Сходимость: {'да' if res.converged else 'НЕТ'}\n"
                 f"Q вход  = {d.get('left', 0):.4g} м²/сут\n"
                 f"Q выход = {d.get('right', 0):.4g}\n"
                 f"Q низ   = {d.get('bottom', 0):.2g} (≈0)\n"
                 f"Невязка = {d.get('imbalance', 0):.2g}")
        self.redraw_filtration()
        self.project.results["filtration"] = {"Q_in": d.get("left"), "Q_out": d.get("right"),
                                              "iterations": res.iterations,
                                              "converged": res.converged}
        if res.warnings:
            messagebox.showwarning("Замечания расчёта", "\n".join(res.warnings))

    def redraw_filtration(self):
        c = self.filt_canvas
        c.overlay.clear()

        # ГУ на контуре показываем всегда
        bc_vis = []
        for b in self.custom_bc:
            if not b.segment:
                continue
            color = {"head": COLORS["bc_head"],
                     "seepage": COLORS["bc_seepage"],
                     "water_level": COLORS["water"]}.get(b.kind, COLORS["bc_impermeable"])
            label = f"H={b.value:g} м" if b.kind == "head" else b.kind
            bc_vis.append((b.segment[0], b.segment[1], color, label))
        if bc_vis:
            c.overlay["bc"] = bc_vis

        res = self.filt_result
        if res is not None and hasattr(res, "equipotentials"):
            polylines = []
            if self.show_eq.get():
                segs, labels = [], []
                for lev, sl in res.equipotentials:
                    for p1, p2, _ in sl:
                        segs.append((p1, p2, COLORS["equipotential"], 1))
                    if self.show_lbl.get() and sl:
                        p1, p2, _ = sl[len(sl) // 2]
                        labels.append(((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2,
                                       f"{lev:.1f}", COLORS["equipotential"]))
                if segs:
                    c.overlay["segments"] = segs
                if labels:
                    c.overlay["labels"] = labels
            if self.show_sl.get():
                polylines.extend((l, COLORS["streamline"], 2) for l in res.streamlines)
            if self.show_vec.get():
                c.overlay["vectors"] = res.vectors_for_canvas()
            if self.show_dep.get() and res.depression:
                c.overlay["curve"] = (res.depression, COLORS["depression"], 3, None)
            if res.seepage_segments:
                polylines.extend((s, COLORS["seepage_zone"], 5) for s in res.seepage_segments)
            if polylines:
                c.overlay["polylines"] = polylines
        c.redraw()

    def calculate_thermal(self):
        if len(self.project.points) < 3:
            messagebox.showwarning("Внимание", "Нарисуйте контур на вкладке 1.")
            return
        try:
            t_air = float(self.ent_t_air.get().replace(",", "."))
            days = int(self.ent_days.get())
            t_init = float(self.ent_t_init.get().replace(",", "."))
            dx = float(self.ent_dx.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Ошибка", "Проверьте параметры промерзания.")
            return
        self.project.thermal = {"t_air": t_air, "days": days, "t_init": t_init}
        self.copy_slope_geo()
        calc = ThermalCalculator(self.project.points, self.project.layers)
        self.root.config(cursor="watch")
        self.root.update()
        try:
            curve = calc.solve(T_air=t_air, T_init=t_init, days=days, delta_x=dx)
        finally:
            self.root.config(cursor="")
        if curve:
            depth = calc.max_freeze_depth(curve)
            self.therm_info.config(text=f"Глубина промерзания ≈ {depth:.2f} м\n"
                                        f"Точек границы: {len(curve)}")
            self.therm_canvas.overlay["curve"] = (curve, COLORS["freeze"], 4, None)
            self.therm_canvas.redraw()
            self.project.results["thermal"] = {"max_depth": depth}
        else:
            self.therm_info.config(text="Граница фазового перехода не найдена.")

    # ------------------------------------------------------------- проекты/IO
    def new_project(self):
        if not messagebox.askyesno("Новый проект",
                                   "Все несохранённые данные будут потеряны. Продолжить?"):
            return
        self.project = Project()
        self.history.clear()
        self.custom_bc = []
        self.filt_result = None
        self.last_result = None
        for c in (self.canvas, self.filt_canvas, self.therm_canvas):
            c.overlay.clear()
        self.refresh_all()
        self.result_fs.config(text="FS = --", foreground="gray")
        self.result_status.config(text="Нет данных", foreground="black")
        self.calc_info.config(text="")
        self.filt_info.config(text="")
        self.root.title(f"{APP_NAME} {APP_VERSION} — калькулятор устойчивости откосов")

    def save_project(self):
        self.project.filtration_bc = dict(self.project.filtration_bc or {})
        self.project.filtration_bc["custom"] = [b.to_dict() for b in self.custom_bc]
        if not self.project.path:
            return self.save_project_as()
        try:
            self.project.save()
            messagebox.showinfo("Сохранено", f"Проект сохранён:\n{self.project.path}")
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))

    def save_project_as(self):
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("Проект Gravey", "*.json")])
        if not path:
            return
        try:
            self.project.save(path)
            self.root.title(f"{APP_NAME} — {self.project.name}")
            messagebox.showinfo("Сохранено", f"Проект сохранён:\n{path}")
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))

    def load_project(self):
        path = filedialog.askopenfilename(filetypes=[("Проект Gravey", "*.json")])
        if not path:
            return
        try:
            self.project = Project.load(path)
            self.history.clear()
            self.filt_result = None
            self.last_result = None
            for c in (self.canvas, self.filt_canvas, self.therm_canvas):
                c.overlay.clear()
            self.refresh_all()
            self.canvas.fit_view()
            self.root.title(f"{APP_NAME} — {self.project.name}")
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось загрузить: {exc}")

    def save_report(self):
        if not self.last_result:
            messagebox.showwarning("Внимание", "Сначала выполните расчёт FS.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Текстовый отчёт", "*.txt")])
        if not path:
            return
        r = self.last_result
        head, rows = self.calculator.slices_table(r)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{APP_NAME} {APP_VERSION} — ОТЧЁТ ОБ УСТОЙЧИВОСТИ ОТКОСА\n")
            f.write("=" * 60 + "\n")
            f.write(f"Проект: {self.project.name}\n")
            f.write(f"Тип объекта: {OBJ_TYPE_LABELS.get(self.project.obj_type, '')}\n")
            f.write(f"Метод: {STABILITY_METHODS['bishop']}\n")
            f.write(f"FS = {r.fs:.3f}   (требуется ≥ {r.required_fs})\n")
            f.write(f"Статус: {'УСТОЙЧИВО' if r.stable else 'НЕ УСТОЙЧИВО'}\n")
            f.write(f"Центр дуги: ({r.cx:.3f}; {r.cy:.3f}), R = {r.r:.3f} м\n")
            f.write(f"Основной грунт: γ={self.project.soil['gamma']}, "
                    f"c={self.project.soil['c']}, φ={self.project.soil['phi']}\n")
            f.write("\nСЛОИ:\n")
            for l in self.project.layers:
                f.write(f"  {l.number}. {l.name}: γ={l.gamma}, c={l.c}, φ={l.phi}, "
                        f"k_x={l.k_x}, k_z={l.k_z}, α={l.anisotropy_angle}, "
                        f"h_cap={l.h_cap}, {'вкл' if l.enabled else 'выкл'}\n")
            f.write("\nТАБЛИЦА СРЕЗОВ:\n")
            f.write(" | ".join(head) + "\n")
            for row in rows:
                f.write(" | ".join(str(v) for v in row) + "\n")
            if self.filt_result is not None and hasattr(self.filt_result, "discharge"):
                d = self.filt_result.discharge
                f.write("\nФИЛЬТРАЦИЯ:\n")
                f.write(f"  Итераций: {self.filt_result.iterations}, "
                        f"невязка кривой: {self.filt_result.curve_residual:.4f} м, "
                        f"сходимость: {'да' if self.filt_result.converged else 'нет'}\n")
                f.write(f"  Q вход={d.get('left', 0):.6g}, Q выход={d.get('right', 0):.6g}, "
                        f"Q низ={d.get('bottom', 0):.3g}, невязка={d.get('imbalance', 0):.3g} м²/сут\n")
        messagebox.showinfo("Готово", "Отчёт сохранён.")

    def export_csv(self):
        if not self.last_result:
            messagebox.showwarning("Внимание", "Сначала выполните расчёт FS.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        head, rows = self.calculator.slices_table(self.last_result)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([f"FS={self.last_result.fs:.4f}",
                        f"требуется>={self.last_result.required_fs}"])
            w.writerow(head)
            w.writerows(rows)
        messagebox.showinfo("Готово", "Таблица срезов сохранена (CSV).")

    def export_discharge_csv(self):
        if self.filt_result is None or not hasattr(self.filt_result, "point_discharges"):
            messagebox.showwarning("Внимание", "Сначала выполните расчёт фильтрации.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        r = self.filt_result
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["РАСХОДЫ ЧЕРЕЗ ГРАНИЦЫ, м²/сут на 1 м длины"])
            for k, title in (("left", "Вход (левая)"), ("right", "Выход (правая)"),
                             ("bottom", "Низ (должен быть ≈0)"), ("top", "Верх"),
                             ("imbalance", "Невязка баланса")):
                w.writerow([title, f"{r.discharge.get(k, 0):.6g}"])
            w.writerow([])
            w.writerow(["Итераций", r.iterations,
                        "Невязка кривой, м", f"{r.curve_residual:.6g}",
                        "Сходимость", "да" if r.converged else "нет"])
            w.writerow([])
            w.writerow(["РАСХОД В ТОЧКАХ (закон Дарси)"])
            w.writerow(["x, м", "y, м", "H, м", "vx, м/сут", "vy, м/сут",
                        "|v|, м/сут", "k_xx", "k_zz"])
            for p in r.point_discharges:
                w.writerow([f"{p['x']:.3f}", f"{p['y']:.3f}", f"{p['H']:.3f}",
                            f"{p['vx']:.6g}", f"{p['vy']:.6g}", f"{p['v']:.6g}",
                            f"{p['k_x']:.6g}", f"{p['k_z']:.6g}"])
        messagebox.showinfo("Готово", "Таблица расходов сохранена.")

    def show_help(self):
        win = tk.Toplevel(self.root)
        win.title("Инструкция (F1)")
        win.geometry("680x560")
        txt = tk.Text(win, wrap=tk.WORD, font=("Consolas", 10))
        txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        extra = """
6. ФИЛЬТРАЦИЯ (вкладка 2)
   • «Выровнять границы» — боковые грани строго вертикальны (обязательно!).
   • Напор слева/справа задаётся В МЕТРАХ ОТ НИЖНЕЙ границы.
   • Низ всегда непроницаемый: ∂H/∂n = 0.
   • «Границы на контуре» → клик по сегменту → тип ГУ
     (напор / высачивание H=z / уровень воды / непроницаемая).
   • Галочка «Правая грань проницаема» даёт участок высачивания.
   • Результат — гидродинамическая сетка: кривая депрессии,
     линии равных напоров, линии тока. Плюс расходы через границы.
   • Кнопка «Методика» — какие уравнения и библиотеки использованы.

7. СВОЙСТВА СЛОЯ
   Двойной клик по слою. Галочка «Брать из справочника» блокирует поля;
   снимите её, чтобы ввести лабораторные k_x, k_z, угол α и др.
"""
        txt.insert("1.0", HELP_TEXT + extra)
        txt.config(state=tk.DISABLED)
        ttk.Button(win, text="Закрыть", command=win.destroy).pack(pady=6)


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()