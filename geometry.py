# geometry.py
"""Геометрия модели: контур, слои, УГВ, поверхности скольжения, сетки."""
import math

EPS = 1e-9


# ------------------------------------------------------------------ базовое
def polygon_area(points):
    """Площадь полигона (без знака)."""
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def polygon_centroid(points):
    n = len(points)
    if n == 0:
        return (0.0, 0.0)
    if n < 3:
        return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)
    a = cx = cy = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a) < EPS:
        return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)
    a *= 0.5
    return (cx / (6 * a), cy / (6 * a))


def bounds(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def is_point_in_poly(pt, polygon):
    """Ray-casting. Точка на границе может считаться как внутри, так и снаружи."""
    if not polygon or len(polygon) < 3:
        return False
    x, y = pt
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xin:
                inside = not inside
    return inside


def distance_to_segment(pt, p1, p2):
    x0, y0 = pt
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    if abs(dx) < EPS and abs(dy) < EPS:
        return math.hypot(x0 - x1, y0 - y1)
    t = ((x0 - x1) * dx + (y0 - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(x0 - (x1 + t * dx), y0 - (y1 + t * dy))


def distance_to_polyline(pt, line, closed=False):
    if len(line) < 2:
        return math.hypot(pt[0] - line[0][0], pt[1] - line[0][1]) if line else float("inf")
    n = len(line)
    rng = range(n) if closed else range(n - 1)
    return min(distance_to_segment(pt, line[i], line[(i + 1) % n]) for i in rng)


def segment_intersection(p1, p2, p3, p4):
    """Точка пересечения отрезков или None."""
    x1, y1 = p1; x2, y2 = p2
    x3, y3 = p3; x4, y4 = p4
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < EPS:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / den
    if -EPS <= t <= 1 + EPS and -EPS <= u <= 1 + EPS:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


def polyline_y_at(line, x, default=None):
    """Ордината ломаной в точке x (ломаная может быть немонотонной по X)."""
    if not line:
        return default
    if len(line) == 1:
        return line[0][1]
    vals = []
    for i in range(len(line) - 1):
        x1, y1 = line[i]
        x2, y2 = line[i + 1]
        if min(x1, x2) - EPS <= x <= max(x1, x2) + EPS:
            if abs(x2 - x1) < EPS:
                vals.extend([y1, y2])
            else:
                t = (x - x1) / (x2 - x1)
                vals.append(y1 + t * (y2 - y1))
    if not vals:
        return default
    return max(vals)          # верхняя ветвь — свободная поверхность


def sort_polyline(line):
    return sorted(line, key=lambda p: p[0])


# --------------------------------------------------------------- модель
class SlopeGeometry:
    """Контур сооружения + вспомогательные геометрические запросы."""

    def __init__(self, points):
        self.points = [tuple(map(float, p)) for p in points]

    def __len__(self):
        return len(self.points)

    @property
    def valid(self):
        return len(self.points) >= 3

    @property
    def bounds(self):
        return bounds(self.points)

    @property
    def width(self):
        x0, _, x1, _ = self.bounds
        return x1 - x0

    @property
    def height(self):
        _, y0, _, y1 = self.bounds
        return y1 - y0

    def contains(self, pt):
        return is_point_in_poly(pt, self.points)

    def vertical_intersections(self, x):
        """Все пересечения вертикали x с контуром (отсортированы)."""
        ys = []
        n = len(self.points)
        for i in range(n):
            x1, y1 = self.points[i]
            x2, y2 = self.points[(i + 1) % n]
            if min(x1, x2) - EPS <= x <= max(x1, x2) + EPS and abs(x2 - x1) > EPS:
                t = (x - x1) / (x2 - x1)
                ys.append(y1 + t * (y2 - y1))
        ys.sort()
        # убираем дубликаты в вершинах
        out = []
        for y in ys:
            if not out or abs(y - out[-1]) > 1e-7:
                out.append(y)
        return out

    def get_slope_height(self, x):
        """(низ, верх) тела откоса на абсциссе x либо (None, None)."""
        ys = self.vertical_intersections(x)
        if not ys:
            return None, None
        return ys[0], ys[-1]

    def surface_y(self, x):
        return self.get_slope_height(x)[1]

    def clip_vertical(self, x, y_low, y_high):
        """Обрезка вертикального интервала телом откоса."""
        bot, top = self.get_slope_height(x)
        if bot is None:
            return None
        lo, hi = max(y_low, bot), min(y_high, top)
        return (lo, hi) if hi - lo > EPS else None


class WaterTable:
    """Уровень грунтовых вод — ломаная линия (по дополнению к ТЗ)."""

    def __init__(self, points):
        self.points = sort_polyline([tuple(map(float, p)) for p in (points or [])])

    def __bool__(self):
        return len(self.points) > 0

    def y_at(self, x, default=-1e9):
        return polyline_y_at(self.points, x, default=default)

    def is_below(self, x, y):
        wy = self.y_at(x)
        return wy > -1e8 and y < wy

    def head_at(self, x, datum=0.0):
        """Пьезометрический напор относительно отметки datum (низ модели)."""
        wy = self.y_at(x)
        return max(0.0, wy - datum) if wy > -1e8 else 0.0


# --------------------------------------------------- поверхности скольжения
def circle_points(cx, cy, r, steps=180, a0=0.0, a1=360.0):
    pts = []
    for i in range(steps + 1):
        a = math.radians(a0 + (a1 - a0) * i / steps)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def circle_lower_y(cx, cy, r, x):
    d = r * r - (x - cx) ** 2
    if d < 0:
        return None
    return cy - math.sqrt(d)


def arc_inside_polygon(cx, cy, r, geom, steps=360):
    """Часть окружности, лежащая внутри тела откоса (для отрисовки)."""
    segments, cur = [], []
    for i in range(steps + 1):
        a = math.radians(i)
        mx, my = cx + r * math.cos(a), cy + r * math.sin(a)
        bot, top = geom.get_slope_height(mx)
        if bot is not None and bot - 1e-6 <= my <= top + 1e-6:
            cur.append((mx, my))
        else:
            if len(cur) > 1:
                segments.append(cur)
            cur = []
    if len(cur) > 1:
        segments.append(cur)
    return segments


def circle_from_three_points(p1, p2, p3):
    """Окружность по трём точкам -> (cx, cy, r) или None (для ручного режима)."""
    ax, ay = p1; bx, by = p2; cx_, cy_ = p3
    d = 2 * (ax * (by - cy_) + bx * (cy_ - ay) + cx_ * (ay - by))
    if abs(d) < EPS:
        return None
    ux = ((ax ** 2 + ay ** 2) * (by - cy_) + (bx ** 2 + by ** 2) * (cy_ - ay)
          + (cx_ ** 2 + cy_ ** 2) * (ay - by)) / d
    uy = ((ax ** 2 + ay ** 2) * (cx_ - bx) + (bx ** 2 + by ** 2) * (ax - cx_)
          + (cx_ ** 2 + cy_ ** 2) * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


# ------------------------------------------------------------------ сетки
def regular_grid(xmin, ymin, xmax, ymax, dx, dy=None):
    dy = dx if dy is None else dy
    nx = max(2, int(round((xmax - xmin) / dx)) + 1)
    ny = max(2, int(round((ymax - ymin) / dy)) + 1)
    xs = [xmin + i * (xmax - xmin) / (nx - 1) for i in range(nx)]
    ys = [ymin + j * (ymax - ymin) / (ny - 1) for j in range(ny)]
    return xs, ys


def adaptive_axis(vmin, vmax, base_step, extra_coords=(), refine=0.5):
    """Переменные шаги Δx_i / Δy_j: сгущение у заданных отметок (границы слоёв)."""
    coords = {vmin, vmax}
    n = max(2, int(round((vmax - vmin) / base_step)) + 1)
    for i in range(n):
        coords.add(vmin + i * (vmax - vmin) / (n - 1))
    for v in extra_coords:
        if vmin < v < vmax:
            coords.add(v)
            for k in (1, 2):
                d = base_step * (refine ** k)
                if v - d > vmin:
                    coords.add(v - d)
                if v + d < vmax:
                    coords.add(v + d)
    return sorted(coords)