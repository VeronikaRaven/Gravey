# main.py
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import math
import json
from config import *
from calculator import SlopeCalculator
from filtration import FiltrationCalculator
from thermal import ThermalCalculator


class SlopeCanvas(tk.Canvas):
    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, bg=COLORS["canvas_bg"], **kwargs)
        self.app = app
        self.points = []
        self.water_points = []

        # НОВОЕ: Списки для внутренних фигур (включений)
        self.inclusions = []  # Завершенные фигуры
        self.current_inclusion = []  # Текущая рисуемая фигура
        self.inclusion_mode = False  # Флаг режима рисования включений

        self.scale = DEFAULT_SCALE
        self.offset_x = 100
        self.offset_y = 500

        self.drag_point_idx = None
        self.drag_water_idx = None
        self.water_mode = False
        self.history = []
        self.read_only = False
        self.soil_color = "#ffffff"

        self.bind("<Button-1>", self.on_left_click)
        self.bind("<Button-3>", self.on_right_click)
        self.bind("<B1-Motion>", self.on_left_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Motion>", self.on_mouse_move)
        self.bind("<MouseWheel>", self.on_zoom)
        self.bind("<Button-4>", self.on_zoom)
        self.bind("<Button-5>", self.on_zoom)

        self.draw()

    def model_to_screen(self, x, y):
        return self.offset_x + x * self.scale, self.offset_y - y * self.scale

    def screen_to_model(self, sx, sy):
        return (sx - self.offset_x) / self.scale, (self.offset_y - sy) / self.scale

    def is_point_in_poly(self, pt, polygon):
        """Проверяет, находится ли точка внутри полигона"""
        if not polygon: return False
        x, y = pt
        n = len(polygon)
        inside = False
        for i in range(n):
            x1, y1 = polygon[i]
            x2, y2 = polygon[(i + 1) % n]
            if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
                inside = not inside
        return inside

    def save_state(self):
        state = {'points': self.points.copy(), 'water': self.water_points.copy()}
        self.history.append(state)
        if len(self.history) > 20: self.history.pop(0)

    def undo(self):
        if self.history:
            state = self.history.pop()
            self.points = state['points']
            self.water_points = state['water']
            self.draw()

    def draw(self):
        self.delete("all")
        self.draw_grid()
        self.draw_axes()
        self.draw_slope()
        self.draw_layers()
        self.draw_inclusions()  # НОВОЕ: Рисуем внутренние фигуры
        self.draw_water()

    def draw_grid(self):
        w = self.winfo_width() if self.winfo_width() > 1 else CANVAS_WIDTH
        h = self.winfo_height() if self.winfo_height() > 1 else CANVAS_HEIGHT
        step = GRID_STEP_M * self.scale
        if step < 10: step *= 5

        start_x = self.offset_x % step
        for x in range(int(start_x), w, int(step)):
            self.create_line(x, 0, x, h, fill=COLORS["grid"])

        start_y = self.offset_y % step
        for y in range(int(start_y), h, int(step)):
            self.create_line(0, y, w, y, fill=COLORS["grid"])

    def draw_axes(self):
        w = self.winfo_width() if self.winfo_width() > 1 else CANVAS_WIDTH
        h = self.winfo_height() if self.winfo_height() > 1 else CANVAS_HEIGHT
        self.create_line(0, self.offset_y, w, self.offset_y, arrow=tk.LAST, fill=COLORS["axis"], width=2)
        self.create_line(self.offset_x, h, self.offset_x, 0, arrow=tk.LAST, fill=COLORS["axis"], width=2)
        self.create_text(w - 20, self.offset_y + 15, text="X, м", fill="black")
        self.create_text(self.offset_x - 15, 20, text="Y, м", fill="black")
        self.create_text(self.offset_x - 10, self.offset_y + 10, text="0", fill="black")

    def draw_slope(self):
        if len(self.points) > 1:
            screen_pts = [self.model_to_screen(x, y) for x, y in self.points]
            screen_pts.append(screen_pts[0])
            self.create_polygon(screen_pts, outline=COLORS["slope"], fill=self.soil_color, stipple="gray25", width=2)

        for i, (x, y) in enumerate(self.points):
            sx, sy = self.model_to_screen(x, y)
            self.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, fill="red")
            self.create_text(sx + 10, sy - 10, text=str(i + 1), fill="red", font=("Arial", 8))

    def draw_layers(self):
        if len(self.points) < 3 or not self.app.layers: return
        for layer in self.app.layers:
            y_bot = layer['y_bottom']
            y_top = layer['y_top']
            color = layer['color']
            left_pts, right_pts = [], []
            x_coords = [p[0] for p in self.points]
            xmin, xmax = min(x_coords), max(x_coords)

            for i in range(100):
                x = xmin + (xmax - xmin) * i / 99
                y1, y2 = self.app.calculator.get_slope_height(x)
                if y1 is not None and max(y1, y_bot) < min(y2, y_top):
                    left_pts.append((x, max(y1, y_bot)))
                    right_pts.append((x, min(y2, y_top)))

            if len(left_pts) > 1:
                poly_pts = []
                for p in left_pts:
                    poly_pts.extend(self.model_to_screen(p[0], p[1]))
                for p in reversed(right_pts):
                    poly_pts.extend(self.model_to_screen(p[0], p[1]))
                if len(poly_pts) >= 6:
                    self.create_polygon(poly_pts, fill=color, outline="", stipple="gray50")

    def draw_inclusions(self):
        """НОВОЕ: Рисует внутренние фигуры (бетон, линзы грунта и т.д.)"""
        # Рисуем все готовые включения
        for inc in self.inclusions:
            if len(inc) > 2:
                screen_pts = [self.model_to_screen(x, y) for x, y in inc]
                self.create_polygon(screen_pts, outline="#555555", fill="#A9A9A9", stipple="gray50", width=2)

        # Рисуем текущую фигуру, которую пользователь только что начал
        if self.current_inclusion:
            screen_pts = [self.model_to_screen(x, y) for x, y in self.current_inclusion]
            # Линии
            if len(screen_pts) > 1:
                flat = [c for p in screen_pts for c in p]
                self.create_line(flat, fill="orange", width=2, dash=(4, 2))
            # Точки
            for x, y in self.current_inclusion:
                sx, sy = self.model_to_screen(x, y)
                self.create_oval(sx - 3, sy - 3, sx + 3, sy + 3, fill="orange")

    def draw_water(self):
        if len(self.water_points) > 1:
            screen_pts = [self.model_to_screen(x, y) for x, y in self.water_points]
            self.create_line(screen_pts, fill=COLORS["water"], width=2, dash=(4, 2))
        for x, y in self.water_points:
            sx, sy = self.model_to_screen(x, y)
            self.create_oval(sx - 3, sy - 3, sx + 3, sy + 3, fill="blue")

    def draw_slip_arcs(self, circles):
        if not circles: return
        x_coords = [p[0] for p in self.points]
        xmin, xmax = min(x_coords), max(x_coords)

        for idx, (fs, cx, cy, r) in enumerate(circles):
            color = "red" if idx == 0 else "#ffb3b3"
            width = 3 if idx == 0 else 1
            points = []
            steps = 200
            for i in range(steps + 1):
                mx = xmin + (xmax - xmin) * i / steps
                dx = mx - cx
                if abs(dx) > r: continue
                my = cy - math.sqrt(r ** 2 - dx ** 2)
                y_bot, y_top = self.app.calculator.get_slope_height(mx)
                if y_bot is not None and y_bot <= my <= y_top:
                    sx, sy = self.model_to_screen(mx, my)
                    points.append((sx, sy))
            if len(points) > 1:
                flat_points = [coord for pt in points for coord in pt]
                self.create_line(flat_points, fill=color, width=width, smooth=True)

    def draw_custom_curve(self, points, color="purple", width=2, dash=None):
        if not points: return
        screen_pts = []
        for x, y in points:
            sx, sy = self.model_to_screen(x, y)
            screen_pts.extend([sx, sy])
        if len(screen_pts) > 2:
            self.create_line(screen_pts, fill=color, width=width, dash=dash, smooth=True)

    def on_left_click(self, event):
        if self.read_only: return
        mx, my = self.screen_to_model(event.x, event.y)

        # НОВОЕ: Если мы в режиме рисования включения
        if self.inclusion_mode:
            # Проверяем, что кликнули ВНУТРИ откоса
            if len(self.points) < 3:
                messagebox.showwarning("Ошибка", "Сначала нарисуйте основной контур откоса!")
                return

            if self.is_point_in_poly((mx, my), self.points):
                self.current_inclusion.append((mx, my))
                self.draw()
            else:
                # Если кликнули снаружи — предупреждаем
                self.app.info_label.config(text="Ошибка: Включение должно быть ВНУТРИ откоса!")
            return

        # Стандартный режим рисования точек контура
        for i, (px, py) in enumerate(self.points):
            if math.hypot(px - mx, py - my) * self.scale < 15:
                self.drag_point_idx = i
                self.save_state()
                return
        for i, (px, py) in enumerate(self.water_points):
            if math.hypot(px - mx, py - my) * self.scale < 15:
                self.drag_water_idx = i
                self.save_state()
                return

        self.save_state()
        if self.water_mode:
            self.water_points.append((mx, my))
        else:
            self.points.append((mx, my))
        self.draw()

    def on_right_click(self, event):
        if self.read_only: return
        mx, my = self.screen_to_model(event.x, event.y)
        for i, (px, py) in enumerate(self.points):
            if math.hypot(px - mx, py - my) * self.scale < 15:
                self.save_state()
                del self.points[i]
                self.draw()
                return

    def on_left_drag(self, event):
        if self.read_only: return
        mx, my = self.screen_to_model(event.x, event.y)
        if self.drag_point_idx is not None:
            self.points[self.drag_point_idx] = (mx, my)
            self.draw()
        elif self.drag_water_idx is not None:
            self.water_points[self.drag_water_idx] = (mx, my)
            self.draw()

    def on_release(self, event):
        self.drag_point_idx = None
        self.drag_water_idx = None

    def on_mouse_move(self, event):
        mx, my = self.screen_to_model(event.x, event.y)
        self.app.info_label.config(text=f"Координаты: X={mx:.2f} м, Y={my:.2f} м | Масштаб: 1 м = {self.scale:.0f} px")

    def on_zoom(self, event):
        if event.num == 4 or getattr(event, 'delta', 0) > 0:
            factor = 1.1
        elif event.num == 5 or getattr(event, 'delta', 0) < 0:
            factor = 0.9
        else:
            return
        self.scale *= factor
        self.draw()

    def reset_scale(self):
        self.scale = DEFAULT_SCALE
        self.draw()


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Геотехнический калькулятор (Прототип)")
        self.root.geometry("1300x800")

        self.layers = []

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.create_menu()
        self.create_stability_tab()
        self.create_filtration_tab()
        self.create_thermal_tab()

        self.root.bind_all("<Control-z>", self.global_undo)

    def global_undo(self, event=None):
        current_tab = self.notebook.index(self.notebook.select())
        if current_tab == 0:
            self.canvas.undo()
        elif current_tab == 1:
            self.filt_canvas.undo()
        elif current_tab == 2:
            self.therm_canvas.undo()

    def create_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Проект", menu=file_menu)
        file_menu.add_command(label="Сохранить проект (JSON)", command=self.save_project)
        file_menu.add_command(label="Загрузить проект (JSON)", command=self.load_project)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self.root.quit)

        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Редактирование", menu=edit_menu)
        edit_menu.add_command(label="Отменить (Ctrl+Z)", command=self.global_undo)

    def create_stability_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="1. Устойчивость откоса")

        left_panel = ttk.Frame(tab, width=260)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left_panel.pack_propagate(False)

        tools_frame = ttk.LabelFrame(left_panel, text="Инструменты", padding=5)
        tools_frame.pack(fill=tk.X, pady=5)
        ttk.Button(tools_frame, text="Очистить всё", command=self.clear_canvas).pack(fill=tk.X, pady=2)
        ttk.Button(tools_frame, text="Режим УГВ (Вода)", command=self.toggle_water_mode).pack(fill=tk.X, pady=2)

        # НОВОЕ: Кнопки для рисования внутренних фигур
        ttk.Button(tools_frame, text="Нарисовать включение (внутри)", command=self.toggle_inclusion_mode).pack(
            fill=tk.X, pady=2)
        ttk.Button(tools_frame, text="Завершить включение", command=self.finish_inclusion).pack(fill=tk.X, pady=2)

        ttk.Button(tools_frame, text="Сбросить масштаб", command=lambda: self.canvas.reset_scale()).pack(fill=tk.X,
                                                                                                         pady=2)

        soil_frame = ttk.LabelFrame(left_panel, text="Свойства основного грунта", padding=5)
        soil_frame.pack(fill=tk.X, pady=5)

        ttk.Label(soil_frame, text="Удельный вес γ (кН/м³):").pack(anchor=tk.W)
        self.ent_gamma = ttk.Entry(soil_frame)
        self.ent_gamma.insert(0, "19.0")
        self.ent_gamma.pack(fill=tk.X, pady=2)

        ttk.Label(soil_frame, text="Сцепление c (кПа):").pack(anchor=tk.W)
        self.ent_c = ttk.Entry(soil_frame)
        self.ent_c.insert(0, "15.0")
        self.ent_c.pack(fill=tk.X, pady=2)

        ttk.Label(soil_frame, text="Угол трения φ (°):").pack(anchor=tk.W)
        self.ent_phi = ttk.Entry(soil_frame)
        self.ent_phi.insert(0, "18.0")
        self.ent_phi.pack(fill=tk.X, pady=2)

        self.soil_color_box = tk.Label(soil_frame, text="Суглинок", bg="#FFB3BA", font=("Arial", 9, "bold"))
        self.soil_color_box.pack(fill=tk.X, pady=5)

        self.ent_c.bind("<KeyRelease>", self.update_soil_color)
        self.ent_phi.bind("<KeyRelease>", self.update_soil_color)

        layer_frame = ttk.LabelFrame(left_panel, text="Доп. слои (Гравий и др.)", padding=5)
        layer_frame.pack(fill=tk.X, pady=5)

        ttk.Button(layer_frame, text="+ Добавить слой", command=self.add_layer).pack(fill=tk.X, pady=2)

        self.layer_listbox = tk.Listbox(layer_frame, height=4, font=("Arial", 9))
        self.layer_listbox.pack(fill=tk.X, pady=5)

        ttk.Button(layer_frame, text="Удалить выбранный слой", command=self.delete_selected_layer).pack(fill=tk.X,
                                                                                                        pady=2)

        type_frame = ttk.LabelFrame(left_panel, text="Тип объекта", padding=5)
        type_frame.pack(fill=tk.X, pady=5)
        self.obj_type = tk.StringVar(value="permanent")
        ttk.Radiobutton(type_frame, text="Временный (1.15)", variable=self.obj_type, value="temporary").pack(
            anchor=tk.W)
        ttk.Radiobutton(type_frame, text="Постоянный (1.30)", variable=self.obj_type, value="permanent").pack(
            anchor=tk.W)
        ttk.Radiobutton(type_frame, text="Ответственный (1.50)", variable=self.obj_type, value="responsible").pack(
            anchor=tk.W)

        ttk.Button(left_panel, text="РАССЧИТАТЬ FS", command=self.calculate_stability).pack(fill=tk.X, pady=10,
                                                                                            ipady=10)

        right_panel = ttk.Frame(tab, width=260)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
        right_panel.pack_propagate(False)

        ttk.Label(right_panel, text="Результаты", font=("Arial", 12, "bold")).pack(pady=5)
        self.result_fs = ttk.Label(right_panel, text="FS = --", font=("Arial", 26, "bold"), foreground="gray")
        self.result_fs.pack(pady=10)
        self.result_status = ttk.Label(right_panel, text="Нет данных", font=("Arial", 12))
        self.result_status.pack()

        self.calc_info = ttk.Label(right_panel, text="", font=("Arial", 10), foreground="gray", justify=tk.CENTER)
        self.calc_info.pack(pady=10)

        ttk.Button(right_panel, text="Сохранить отчёт (.txt)", command=self.save_report).pack(fill=tk.X, pady=20)

        self.canvas = SlopeCanvas(tab, self)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.info_label = ttk.Label(self.root, text="Координаты: --", relief=tk.SUNKEN, anchor=tk.W)
        self.info_label.pack(side=tk.BOTTOM, fill=tk.X)

        self.calculator = SlopeCalculator([], [])
        self.update_soil_color()

    def toggle_inclusion_mode(self):
        self.canvas.inclusion_mode = not self.canvas.inclusion_mode
        if self.canvas.inclusion_mode:
            messagebox.showinfo("Включение", "Режим включения ВКЛ. Кликайте внутри откоса, чтобы нарисовать фигуру.")
        else:
            messagebox.showinfo("Включение", "Режим включения ВЫКЛ.")

    def finish_inclusion(self):
        if len(self.canvas.current_inclusion) > 2:
            self.canvas.inclusions.append(self.canvas.current_inclusion)
            self.canvas.current_inclusion = []
            self.canvas.inclusion_mode = False
            self.canvas.draw()
            messagebox.showinfo("Успех", "Включение добавлено!")
        else:
            messagebox.showwarning("Ошибка", "Слишком мало точек для фигуры (нужно минимум 3).")

    def add_layer(self):
        y_bot = simpledialog.askfloat("Слой", "Нижняя граница Y (м):", initialvalue=0.0)
        if y_bot is None: return
        y_top = simpledialog.askfloat("Слой", "Верхняя граница Y (м):", initialvalue=y_bot + 5.0)
        if y_top is None: return

        soil_names = list(SOIL_PRESETS.keys())
        choice = simpledialog.askinteger("Слой", f"Выберите грунт для слоя (1-{len(soil_names)}):\n" + "\n".join(
            f"{i + 1}. {name}" for i, name in enumerate(soil_names)), minvalue=1, maxvalue=len(soil_names))
        if not choice: return

        soil = SOIL_PRESETS[soil_names[choice - 1]]
        self.layers.append({
            'name': soil_names[choice - 1], 'y_bottom': min(y_bot, y_top), 'y_top': max(y_bot, y_top),
            'gamma': soil['gamma'], 'c': soil['c'], 'phi': soil['phi'], 'color': soil['color']
        })
        self.update_layer_list()
        self.canvas.draw()

    def update_layer_list(self):
        self.layer_listbox.delete(0, tk.END)
        for i, l in enumerate(self.layers):
            self.layer_listbox.insert(tk.END, f"{i + 1}. {l['name']} ({l['y_bottom']:.1f}-{l['y_top']:.1f}м)")

    def delete_selected_layer(self):
        selection = self.layer_listbox.curselection()
        if not selection: return
        index = selection[0]
        del self.layers[index]
        self.update_layer_list()
        self.canvas.draw()

    def update_soil_color(self, event=None):
        try:
            c_val = float(self.ent_c.get())
            phi_val = float(self.ent_phi.get())
            soil_name = get_soil_by_params(c_val, phi_val)
            soil_color = SOIL_PRESETS[soil_name]["color"]

            self.soil_color_box.config(text=f"Основной: {soil_name}", bg=soil_color)
            self.canvas.soil_color = soil_color
            self.canvas.draw()
        except ValueError:
            pass

    def create_filtration_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="2. Фильтрация")
        left_panel = ttk.Frame(tab, width=240)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        ttk.Button(left_panel, text="Скопировать контур", command=self.copy_slope_geo).pack(fill=tk.X, pady=5)
        ttk.Button(left_panel, text="РАССЧИТАТЬ ДЕПРЕССИЮ", command=self.calculate_filtration).pack(fill=tk.X, pady=15,
                                                                                                    ipady=10)
        self.filt_canvas = SlopeCanvas(tab, self)
        self.filt_canvas.read_only = True
        self.filt_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

    def create_thermal_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="3. Промерзание/Оттаивание")
        left_panel = ttk.Frame(tab, width=240)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        ttk.Button(left_panel, text="Скопировать контур", command=self.copy_slope_geo).pack(fill=tk.X, pady=5)

        ttk.Label(left_panel, text="Температура воздуха (°C):").pack(anchor=tk.W, pady=(10, 0))
        self.ent_t_air = ttk.Entry(left_panel);
        self.ent_t_air.insert(0, "-20");
        self.ent_t_air.pack(fill=tk.X, pady=2)
        ttk.Label(left_panel, text="Длительность (суток):").pack(anchor=tk.W, pady=(10, 0))
        self.ent_days = ttk.Entry(left_panel);
        self.ent_days.insert(0, "30");
        self.ent_days.pack(fill=tk.X, pady=2)

        ttk.Button(left_panel, text="РАССЧИТАТЬ", command=self.calculate_thermal).pack(fill=tk.X, pady=15, ipady=10)
        self.therm_canvas = SlopeCanvas(tab, self)
        self.therm_canvas.read_only = True
        self.therm_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

    def copy_slope_geo(self):
        current_tab = self.notebook.index(self.notebook.select())
        target_canvas = self.filt_canvas if current_tab == 1 else self.therm_canvas
        target_canvas.points = self.canvas.points.copy()
        target_canvas.water_points = self.canvas.water_points.copy()
        target_canvas.inclusions = self.canvas.inclusions.copy()  # Передаем включения
        target_canvas.soil_color = self.canvas.soil_color
        target_canvas.draw()

    def calculate_filtration(self):
        if len(self.filt_canvas.points) < 3: return
        calc = FiltrationCalculator(self.filt_canvas.points)
        curve = calc.solve()
        if curve:
            self.filt_canvas.draw()
            self.filt_canvas.draw_custom_curve(curve, color="blue", width=3, dash=(6, 4))

    def calculate_thermal(self):
        if len(self.therm_canvas.points) < 3: return
        try:
            t_air = float(self.ent_t_air.get());
            days = int(self.ent_days.get())
        except ValueError:
            return
        calc = ThermalCalculator(self.therm_canvas.points)
        curve = calc.solve(T_air=t_air, days=days)
        if curve:
            self.therm_canvas.draw()
            self.therm_canvas.draw_custom_curve(curve, color="#00bfff", width=4)

    def clear_canvas(self):
        self.canvas.points = []
        self.canvas.water_points = []
        self.canvas.inclusions = []
        self.canvas.current_inclusion = []
        self.canvas.water_mode = False
        self.canvas.history = []
        self.canvas.draw()
        self.result_fs.config(text="FS = --", foreground="gray")
        self.result_status.config(text="Нет данных")
        self.calc_info.config(text="")

    def toggle_water_mode(self):
        self.canvas.water_mode = not self.canvas.water_mode
        if self.canvas.water_mode:
            messagebox.showinfo("Режим УГВ", "Включен режим рисования УГВ.")

    def calculate_stability(self):
        if len(self.canvas.points) < 3: return
        try:
            soil_props = {
                'gamma': float(self.ent_gamma.get()),
                'c': float(self.ent_c.get()),
                'phi': float(self.ent_phi.get())
            }
        except ValueError:
            return

        self.calculator = SlopeCalculator(
            points=self.canvas.points, water_points=self.canvas.water_points,
            obj_type=self.obj_type.get(), layers=self.layers, soil_props=soil_props
        )
        circles = self.calculator.calculate_bishop(num_slices=30)
        if not circles: return

        best_fs, cx, cy, r = circles[0]
        required_fs = self.calculator.required_fs

        if best_fs >= required_fs:
            self.result_fs.config(text=f"FS = {best_fs:.3f}", foreground="green")
            self.result_status.config(text=f"УСТОЙЧИВО\n(Требуется ≥ {required_fs})", foreground="green")
        else:
            self.result_fs.config(text=f"FS = {best_fs:.3f}", foreground="red")
            self.result_status.config(text=f"НЕ УСТОЙЧИВО\n(Требуется ≥ {required_fs})", foreground="red")

        self.calc_info.config(text=f"Метод: Бишоп\nЦентр: ({cx:.1f}, {cy:.1f})\nR = {r:.1f} м")
        self.canvas.draw()
        self.canvas.draw_slip_arcs(circles)

    def save_project(self):
        filename = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
        if not filename: return
        project_data = {
            'points': self.canvas.points, 'water_points': self.canvas.water_points,
            'inclusions': self.canvas.inclusions, 'layers': self.layers,
            'gamma': self.ent_gamma.get(), 'c': self.ent_c.get(), 'phi': self.ent_phi.get(),
            'obj_type': self.obj_type.get(), 't_air': self.ent_t_air.get(), 'days': self.ent_days.get()
        }
        with open(filename, 'w', encoding='utf-8') as f: json.dump(project_data, f, ensure_ascii=False, indent=4)
        messagebox.showinfo("Успех", "Проект сохранен!")

    def load_project(self):
        filename = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if not filename: return
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.canvas.points = [tuple(p) for p in data.get('points', [])]
            self.canvas.water_points = [tuple(p) for p in data.get('water_points', [])]
            self.canvas.inclusions = [[tuple(p) for p in inc] for inc in data.get('inclusions', [])]
            self.layers = data.get('layers', [])

            self.ent_gamma.delete(0, tk.END);
            self.ent_gamma.insert(0, data.get('gamma', '19.0'))
            self.ent_c.delete(0, tk.END);
            self.ent_c.insert(0, data.get('c', '15.0'))
            self.ent_phi.delete(0, tk.END);
            self.ent_phi.insert(0, data.get('phi', '18.0'))
            self.obj_type.set(data.get('obj_type', 'permanent'))
            self.ent_t_air.delete(0, tk.END);
            self.ent_t_air.insert(0, data.get('t_air', '-20'))
            self.ent_days.delete(0, tk.END);
            self.ent_days.insert(0, data.get('days', '30'))

            self.update_layer_list();
            self.update_soil_color();
            self.canvas.draw()
            messagebox.showinfo("Успех", "Проект загружен!")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить: {e}")

    def save_report(self):
        fs_text = self.result_fs.cget("text")
        if "FS = --" in fs_text: return
        filename = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if not filename: return
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("ОТЧЕТ О РАСЧЕТЕ УСТОЙЧИВОСТИ\n" + "=" * 30 + "\n")
            f.write(f"{fs_text}\n{self.result_status.cget('text')}\n")
            f.write(f"Основной тип грунта: {self.soil_color_box.cget('text')}\n")
            f.write("Дополнительные слои:\n")
            for l in self.layers: f.write(f" - {l['name']} (Y: {l['y_bottom']}-{l['y_top']})\n")
            if self.canvas.inclusions: f.write(f"Внутренних включений: {len(self.canvas.inclusions)}\n")
        messagebox.showinfo("Успех", "Отчет сохранен!")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()