# calculator.py
"""Расчёт устойчивости откоса методом предельного равновесия.

Этап 1: единый интерфейс методов + корректная геометрия/слои/вода.
Этап 3: сюда добавляются Янбу, Моргенштерн-Прайс и МКЭ (редукция прочности).
"""
import math
from dataclasses import dataclass, field

from config import SAFETY_FACTORS, GAMMA_W, WATER_FS_REDUCTION
from geometry import SlopeGeometry, WaterTable
from layers import LayerCollection, SoilLayer


@dataclass
class SliceResult:
    index: int
    x: float
    b: float
    y_top: float
    y_bot: float
    alpha_deg: float
    length: float
    W: float
    u: float                 # поровое давление на подошве (кПа)
    N: float
    T: float                 # сдвигающая
    R: float                 # удерживающая
    soil: str
    submerged: bool


@dataclass
class StabilityResult:
    fs: float
    cx: float
    cy: float
    r: float
    method: str = "bishop"
    required_fs: float = 1.30
    slices: list = field(default_factory=list)
    converged: bool = True
    iterations: int = 0

    @property
    def stable(self):
        return self.fs >= self.required_fs


class SlopeCalculator:
    def __init__(self, points, water_points, obj_type="permanent",
                 layers=None, soil_props=None, consider_capillary=False,
                 reduce_norm_for_water=False):
        self.geom = SlopeGeometry(points)
        self.points = self.geom.points
        self.water = WaterTable(water_points)
        self.water_points = self.water.points
        self.obj_type = obj_type
        self.consider_capillary = consider_capillary
        self.reduce_norm_for_water = reduce_norm_for_water

        base = SAFETY_FACTORS[obj_type]
        if reduce_norm_for_water and self.water:
            base *= (1.0 - WATER_FS_REDUCTION)
        self.required_fs = round(base, 3)

        if isinstance(layers, LayerCollection):
            self.layers = layers
        else:
            self.layers = LayerCollection.from_list(layers or [])

        sp = soil_props or {}
        self.default_layer = SoilLayer(
            name="Основной грунт",
            gamma=float(sp.get("gamma", 19.0)),
            c=float(sp.get("c", 15.0)),
            phi=float(sp.get("phi", 18.0)),
            k_c=float(sp.get("k_c", 0.6)),
            k_phi=float(sp.get("k_phi", 0.8)),
            h_cap=float(sp.get("h_cap", 0.0)),
        )

    # ---------------------------------------------------------- утилиты
    def get_slope_height(self, x):
        return self.geom.get_slope_height(x)

    def get_water_y(self, x):
        return self.water.y_at(x, default=-999.0)

    def layer_at(self, x, y):
        return self.layers.layer_at((x, y)) or self.default_layer

    def get_soil_props_at_point(self, x, y):
        l = self.layer_at(x, y)
        return l.gamma, l.c, l.phi

    # --------------------------------------------------- один столбик
    def _slice(self, i, x_mid, b, y_top, y_slip, cx, r):
        h = y_top - y_slip
        if h <= 1e-6:
            return None
        sin_a = (x_mid - cx) / r
        sin_a = max(-0.999, min(0.999, sin_a))
        cos_a = math.sqrt(max(1e-6, 1 - sin_a * sin_a))
        alpha = math.degrees(math.asin(sin_a))

        layer = self.layer_at(x_mid, 0.5 * (y_top + y_slip))
        wt = self.water.y_at(x_mid, default=-1e9)
        submerged = wt > -1e8 and y_slip < wt

        # послойное интегрирование веса (несколько слоёв в столбике)
        W = 0.0
        edges = sorted({y_slip, y_top} |
                       {y for l in self.layers.active()
                        for _, y in l.polygon if y_slip < y < y_top})
        for y0, y1 in zip(edges[:-1], edges[1:]):
            if y1 - y0 <= 1e-9:
                continue
            lay = self.layer_at(x_mid, 0.5 * (y0 + y1))
            sub = wt > -1e8 and 0.5 * (y0 + y1) < wt
            cap = 0.0
            if self.consider_capillary and not sub and wt > -1e8:
                above = 0.5 * (y0 + y1) - wt
                if 0 <= above <= lay.h_cap and lay.h_cap > 0:
                    cap = 1.0 - above / lay.h_cap
            g, _, _ = lay.strength(submerged=sub, capillary_ratio=cap)
            W += g * (y1 - y0) * b

        length = b / cos_a
        # поровое давление на подошве
        if submerged:
            u = GAMMA_W * (wt - y_slip)
        elif self.consider_capillary and wt > -1e8:
            u = layer.capillary_pressure(y_slip - wt)
        else:
            u = 0.0

        _, c_eff, phi_eff = layer.strength(submerged=submerged)
        # анизотропия прочности: c зависит от направления площадки (этап 3 — тензор)
        beta = math.radians(alpha)
        c_an = c_eff * (1.0 + (layer.c_h_factor - 1.0) * math.cos(beta) ** 2)

        N_eff = max(0.0, W * cos_a - u * length)
        R = N_eff * math.tan(math.radians(phi_eff)) + c_an * length
        T = W * sin_a
        return SliceResult(i, x_mid, b, y_top, y_slip, alpha, length,
                           W, u, N_eff, T, R, layer.name, submerged)

    def _slices_for_circle(self, cx, cy, r, n_slices):
        if not self.geom.valid:
            return []
        xmin, _, xmax, _ = self.geom.bounds
        # ограничиваем область телом, где дуга ниже поверхности
        b = (xmax - xmin) / n_slices
        out = []
        for i in range(n_slices):
            xm = xmin + (i + 0.5) * b
            d = r * r - (xm - cx) ** 2
            if d <= 0:
                continue
            y_c = cy - math.sqrt(d)
            y_bot, y_top = self.geom.get_slope_height(xm)
            if y_bot is None or y_c >= y_top:
                continue
            y_slip = max(y_bot, y_c)
            s = self._slice(i, xm, b, y_top, y_slip, cx, r)
            if s:
                out.append(s)
        return out

    # --------------------------------------------------------- Бишоп
    def bishop_fs(self, cx, cy, r, n_slices=30, tol=1e-5, max_iter=60):
        slices = self._slices_for_circle(cx, cy, r, n_slices)
        if len(slices) < 3:
            return None
        driving = sum(s.T for s in slices)
        if driving <= 1e-6:
            return None                      # нет сдвигающей составляющей
        fs = 1.0
        for it in range(max_iter):
            num = 0.0
            for s in slices:
                phi = math.radians(self.layer_at(s.x, 0.5 * (s.y_top + s.y_bot)).phi)
                tan_phi = math.tan(phi)
                m = math.cos(math.radians(s.alpha_deg)) * (1 + tan_phi * math.sin(math.radians(s.alpha_deg)) / fs)
                if m <= 1e-6:
                    m = 1e-6
                # упрощённый Бишоп: (c·b + (W − u·b)·tanφ) / m
                c_eff = s.R - s.N * tan_phi  # выделяем c·l
                num += (c_eff / math.cos(math.radians(s.alpha_deg))
                        + (s.W - s.u * s.b) * tan_phi) / m
            new_fs = num / driving
            if new_fs <= 0:
                return None
            if abs(new_fs - fs) < tol:
                return StabilityResult(new_fs, cx, cy, r, "bishop",
                                       self.required_fs, slices, True, it + 1)
            fs = new_fs
        return StabilityResult(fs, cx, cy, r, "bishop",
                               self.required_fs, slices, False, max_iter)

    def calculate_method(self, method, cx, cy, r, n_slices=30):
        if method == "bishop":
            return self.bishop_fs(cx, cy, r, n_slices)
        raise NotImplementedError(
            f"Метод '{method}' будет реализован на этапе 3 (Янбу, М-П, МКЭ)")

    # ------------------------------------------------ автопоиск критической
    def search_critical(self, method="bishop", n_slices=30,
                        nx=11, ny=11, nr=5, top_n=3, search_region=None):
        if not self.geom.valid:
            return []
        xmin, ymin, xmax, ymax = self.geom.bounds
        w, h = max(xmax - xmin, 1e-3), max(ymax - ymin, 1e-3)

        if search_region:                     # пользовательская область поиска
            rx0, ry0, rx1, ry1 = search_region
        else:
            rx0, ry1 = xmin, xmax
            ry0, ry1 = ymax + 0.1 * h, ymax + 1.6 * h

        results = []
        for i in range(nx):
            cx = rx0 + (rx1 - rx0) * i / max(1, nx - 1)
            for j in range(ny):
                cy = ry0 + (ry1 - ry0) * j / max(1, ny - 1)
                r_toe = min(math.hypot(cx - xmin, cy - ymin),
                            math.hypot(cx - xmax, cy - ymin))
                r_crest = max(math.hypot(cx - xmin, cy - ymax),
                              math.hypot(cx - xmax, cy - ymax))
                for k in range(1, nr + 1):
                    r = r_toe + (r_crest - r_toe) * k / nr
                    res = self.calculate_method(method, cx, cy, r, n_slices)
                    if res and 0.05 < res.fs < 10.0:
                        results.append(res)

        results.sort(key=lambda x: x.fs)
        unique = []
        for res in results:
            if not any(math.hypot(res.cx - u.cx, res.cy - u.cy) < w * 0.08
                       and abs(res.r - u.r) < w * 0.08 for u in unique):
                unique.append(res)
            if len(unique) >= top_n:
                break
        return unique

    # совместимость со старым вызовом
    def calculate_bishop(self, num_slices=30):
        res = self.search_critical("bishop", num_slices)
        return [(r.fs, r.cx, r.cy, r.r) for r in res]

    # ------------------------------------------------------------- отчёт
    def slices_table(self, result):
        head = ["№", "x, м", "b, м", "h, м", "α, °", "l, м",
                "W, кН", "u, кПа", "N', кН", "T, кН", "R, кН", "Грунт", "Ниже УГВ"]
        rows = []
        for s in result.slices:
            rows.append([s.index + 1, f"{s.x:.2f}", f"{s.b:.2f}",
                         f"{s.y_top - s.y_bot:.2f}", f"{s.alpha_deg:.1f}",
                         f"{s.length:.2f}", f"{s.W:.1f}", f"{s.u:.1f}",
                         f"{s.N:.1f}", f"{s.T:.1f}", f"{s.R:.1f}",
                         s.soil, "да" if s.submerged else "нет"])
        return head, rows