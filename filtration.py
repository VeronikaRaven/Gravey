# filtration.py
"""Фильтрация: МКЭ (линейные треугольники) + закон Дарси.

Этап 2:
  * анизотропия k_x/k_z с углом залегания α (тензор проницаемости);
  * строгие ГУ: низ непроницаемый, напор от низа, участки высачивания,
    пользовательские ГУ на сегментах верхнего контура;
  * итерационное построение кривой депрессии (z_d = H_d, критерий ε1);
  * гидродинамическая сетка: депрессия + эквипотенциали + линии тока;
  * расходы через границы и расход в каждой точке (закон Дарси).
"""
import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import Delaunay
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve

from config import FILTRATION_SOLVER_SETTINGS
from geometry import SlopeGeometry, WaterTable, is_point_in_poly, polyline_y_at
from layers import LayerCollection, SoilLayer


# --------------------------------------------------------------- описание ГУ
@dataclass
class BoundaryCondition:
    """ГУ на сегменте контура или на боковой грани."""
    kind: str = "impermeable"      # impermeable | head | seepage | water_level
    value: float = 0.0             # для head — метры ОТ НИЗА модели
    segment: tuple = None          # ((x1,y1),(x2,y2)) — если задано пользователем
    label: str = ""

    def to_dict(self):
        return {"kind": self.kind, "value": self.value,
                "segment": [list(p) for p in self.segment] if self.segment else None,
                "label": self.label}

    @classmethod
    def from_dict(cls, d):
        seg = d.get("segment")
        return cls(kind=d.get("kind", "impermeable"),
                   value=float(d.get("value", 0.0)),
                   segment=tuple(tuple(p) for p in seg) if seg else None,
                   label=d.get("label", ""))


@dataclass
class FiltrationResult:
    nodes: np.ndarray
    elements: np.ndarray
    heads: np.ndarray
    velocities: np.ndarray
    centers: np.ndarray
    speeds: np.ndarray
    depression: list = field(default_factory=list)
    equipotentials: list = field(default_factory=list)
    streamlines: list = field(default_factory=list)
    seepage_segments: list = field(default_factory=list)
    discharge: dict = field(default_factory=dict)
    iterations: int = 0
    curve_residual: float = 0.0
    head_residual: float = 0.0
    converged: bool = False
    bc_summary: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    point_discharges: list = field(default_factory=list)

    def vectors_for_canvas(self):
        mx = self.speeds.max() if self.speeds.size and self.speeds.max() > 0 else 1.0
        return (self.centers, self.velocities[:, 0] / mx * 0.5,
                self.velocities[:, 1] / mx * 0.5, self.speeds)


class FEMFiltrationSolver:
    """Ядро МКЭ. Порядок вызова: generate_mesh -> solve -> постпроцессинг."""

    def __init__(self, settings=None):
        self.cfg = dict(FILTRATION_SOLVER_SETTINGS)
        if settings:
            self.cfg.update(settings)
        self.nodes = np.empty((0, 2))
        self.elements = np.empty((0, 3), dtype=int)
        self.element_k = np.empty((0, 3))     # k_xx, k_zz, k_xz на элемент
        self.heads = None
        self.velocities = None
        self.geom = None
        self.layers = LayerCollection()
        self.warnings = []
        self.bc_list = []
        self._K_base = None
        self._area = None
        self._b = None
        self._c = None

    # ============================================================ 1. СЕТКА
    def generate_mesh(self, contour_points, layers=None, points_per_meter=None):
        ppm = points_per_meter or self.cfg["points_per_meter"]
        self.geom = SlopeGeometry(contour_points)
        if not self.geom.valid:
            raise ValueError("Для построения сетки нужно минимум 3 точки контура")
        self.layers = layers if isinstance(layers, LayerCollection) \
            else LayerCollection.from_list(layers or [])
        self.warnings = []

        xmin, ymin, xmax, ymax = self.geom.bounds
        step = 1.0 / max(0.2, ppm)
        xs = np.arange(xmin, xmax + step * 0.5, step)
        ys = np.arange(ymin, ymax + step * 0.5, step)
        xx, yy = np.meshgrid(xs, ys)
        cand = np.column_stack((xx.ravel(), yy.ravel()))
        pts = [tuple(p) for p in cand if is_point_in_poly(tuple(p), self.geom.points)]
        pts.extend(self.geom.points)

        # сгущение у границ слоёв и у вершин контура
        for l in self.layers.active():
            pts.extend(l.polygon)
            pts.extend(self._densify(l.polygon, step))
        pts.extend(self._densify(self.geom.points, step, closed=True))

        uniq = {}
        for p in pts:
            uniq[(round(float(p[0]), 6), round(float(p[1]), 6))] = (float(p[0]), float(p[1]))
        self.nodes = np.array(list(uniq.values()))
        if len(self.nodes) < 3:
            raise ValueError("Слишком мало узлов для триангуляции")

        tri = Delaunay(self.nodes)
        keep = [e for e in tri.simplices
                if is_point_in_poly(tuple(self.nodes[e].mean(axis=0)), self.geom.points)]
        self.elements = np.array(keep, dtype=int)
        if len(self.elements) == 0:
            raise ValueError("Триангуляция не покрыла область")

        default = SoilLayer()
        self.element_k = np.zeros((len(self.elements), 3))
        for i, e in enumerate(self.elements):
            lay = self.layers.layer_at(tuple(self.nodes[e].mean(axis=0))) or default
            self.element_k[i] = lay.k_tensor()

        self._precompute_geometry()
        self._check_boundary_verticality()
        return len(self.nodes), len(self.elements)

    @staticmethod
    def _densify(poly, step, closed=False):
        out = []
        n = len(poly)
        rng = range(n) if closed else range(n - 1)
        for i in rng:
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
            L = math.hypot(x2 - x1, y2 - y1)
            k = int(L / step)
            for j in range(1, k + 1):
                t = j / (k + 1)
                out.append((x1 + t * (x2 - x1), y1 + t * (y2 - y1)))
        return out

    def _precompute_geometry(self):
        p = self.nodes[self.elements]
        x1, y1 = p[:, 0, 0], p[:, 0, 1]
        x2, y2 = p[:, 1, 0], p[:, 1, 1]
        x3, y3 = p[:, 2, 0], p[:, 2, 1]
        self._area = 0.5 * np.abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1))
        self._b = np.column_stack([y2 - y3, y3 - y1, y1 - y2])
        self._c = np.column_stack([x3 - x2, x1 - x3, x2 - x1])

    def _check_boundary_verticality(self):
        """Руководитель: «всегда граница ровная, не ступеньками»."""
        xmin, ymin, xmax, ymax = self.geom.bounds
        tol = max(1e-6, (xmax - xmin) * 0.02)
        for name, xv in (("левая", xmin), ("правая", xmax)):
            side = [p for p in self.geom.points if abs(p[0] - xv) < tol]
            if len(side) >= 2:
                spread = max(p[0] for p in side) - min(p[0] for p in side)
                if spread > tol:
                    self.warnings.append(
                        f"{name.capitalize()} граница не вертикальна (разброс {spread:.2f} м). "
                        f"Рекомендуется «Выровнять границы».")

    # ================================================ 2. СБОРКА И РЕШЕНИЕ
    def _assemble_base(self):
        n = len(self.nodes)
        K = lil_matrix((n, n))
        good = self._area > 1e-9
        for i in np.where(good)[0]:
            e = self.elements[i]
            b, c = self._b[i], self._c[i]
            kxx, kzz, kxz = self.element_k[i]
            ke = (kxx * np.outer(b, b)
                  + kzz * np.outer(c, c)
                  + kxz * (np.outer(b, c) + np.outer(c, b))) / (4.0 * self._area[i])
            for a in range(3):
                for bb in range(3):
                    K[e[a], e[bb]] += ke[a, bb]
        self._K_base = K.tocsr()

    def _collect_dirichlet(self, water_points, bc_user, depression=None):
        """Возвращает {индекс узла: напор} и текстовое описание ГУ."""
        xmin, ymin, xmax, ymax = self.geom.bounds
        wt = WaterTable(water_points)
        tol_x = max(1e-6, (xmax - xmin) * 0.012)
        tol_y = max(1e-6, (ymax - ymin) * 0.012)
        fixed, summary = {}, []

        left_head = bc_user.get("left_head")
        right_head = bc_user.get("right_head")
        right_seepage = bc_user.get("right_seepage", False)

        # --- боковые грани ---
        for i, (x, y) in enumerate(self.nodes):
            if abs(x - xmin) <= tol_x:
                h = float(left_head) if left_head is not None else wt.head_at(x, datum=ymin)
                if h > 0 and y <= ymin + h + tol_y:
                    fixed[i] = ymin + h
            elif abs(x - xmax) <= tol_x:
                if right_head is not None:
                    if y <= ymin + float(right_head) + tol_y:
                        fixed[i] = ymin + float(right_head)
                elif right_seepage:
                    fixed[i] = y                       # H = z, высачивание
        summary.append(f"Левая грань: напор "
                       f"{left_head if left_head is not None else 'из УГВ'} м от низа")
        summary.append("Правая грань: " + (f"напор {right_head} м" if right_head is not None
                       else ("высачивание H=z" if right_seepage else "непроницаемая")))
        summary.append("Низ: непроницаемый (∂H/∂n = 0)")

        # --- пользовательские ГУ на сегментах контура ---
        for bc in self.bc_list:
            if bc.segment is None:
                continue
            (ax, ay), (bx, by) = bc.segment
            cnt = 0
            for i, (x, y) in enumerate(self.nodes):
                if self._point_near_segment((x, y), (ax, ay), (bx, by), tol_y * 1.5):
                    if bc.kind == "head":
                        fixed[i] = ymin + bc.value
                    elif bc.kind == "seepage":
                        fixed[i] = y
                    elif bc.kind == "water_level":
                        wy = wt.y_at(x, default=-1e9)
                        if wy > -1e8 and y <= wy + tol_y:
                            fixed[i] = wy
                    cnt += 1
            summary.append(f"Сегмент «{bc.label or bc.kind}»: {cnt} узлов")

        # --- затопленная часть поверхности ---
        for i, (x, y) in enumerate(self.nodes):
            surf = self.geom.surface_y(x)
            wy = wt.y_at(x, default=-1e9)
            if surf is not None and abs(y - surf) <= tol_y and wy > surf + tol_y:
                fixed[i] = wy

        # --- кривая депрессии: H = z (свободная поверхность, линия тока) ---
        if depression:
            dep_nodes = self._nodes_on_polyline(depression, tol_y * 2.0)
            for i in dep_nodes:
                fixed[i] = float(self.nodes[i, 1])
            summary.append(f"Кривая депрессии: {len(dep_nodes)} узлов (H = z)")
        return fixed, summary

    @staticmethod
    def _point_near_segment(pt, a, b, tol):
        x0, y0 = pt; x1, y1 = a; x2, y2 = b
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        if L2 < 1e-12:
            return math.hypot(x0 - x1, y0 - y1) <= tol
        t = max(0.0, min(1.0, ((x0 - x1) * dx + (y0 - y1) * dy) / L2))
        return math.hypot(x0 - (x1 + t * dx), y0 - (y1 + t * dy)) <= tol

    def _nodes_on_polyline(self, line, tol):
        idx = []
        for i, (x, y) in enumerate(self.nodes):
            for j in range(len(line) - 1):
                if self._point_near_segment((x, y), line[j], line[j + 1], tol):
                    idx.append(i)
                    break
        return idx

    def _solve_heads(self, fixed):
        if not fixed:
            raise ValueError("Не задано ни одного граничного условия по напору.\n"
                             "Задайте напор на левой грани или УГВ.")
        K = self._K_base.tolil()
        F = np.zeros(len(self.nodes))
        BIG = 1e20
        for i, H in fixed.items():
            K.rows[i] = [i]
            K.data[i] = [BIG]
            F[i] = BIG * H
        h = spsolve(K.tocsr(), F)
        if not np.all(np.isfinite(h)):
            raise ValueError("СЛАУ не решилась: проверьте геометрию и ГУ")
        return h

    # ================================ 3. КРИВАЯ ДЕПРЕССИИ (итерации)
    def _initial_depression(self, water_points, bc_user, npts=40):
        xmin, ymin, xmax, ymax = self.geom.bounds
        wt = WaterTable(water_points)
        h_left = bc_user.get("left_head")
        z_start = ymin + float(h_left) if h_left is not None else wt.y_at(xmin, ymin)
        z_start = min(z_start, self.geom.surface_y(xmin) or ymax)

        rh = bc_user.get("right_head")
        if rh is not None:
            z_end = ymin + float(rh)
        elif bc_user.get("right_seepage"):
            z_end = max(ymin, min(z_start, self.geom.surface_y(xmax) or ymin))
        else:
            z_end = ymin                       # непроницаемая правая грань
        xs = np.linspace(xmin, xmax, npts)
        curve = []
        for x in xs:
            z = z_start + (z_end - z_start) * (x - xmin) / max(xmax - xmin, 1e-9)
            top = self.geom.surface_y(x)
            bot, _ = self.geom.get_slope_height(x)
            if top is None:
                continue
            z = min(max(z, (bot or ymin) + 1e-3), top)
            curve.append((float(x), float(z)))
        return curve

    def solve(self, water_points=None, bc_user=None, custom_bc=None):
        """Полный цикл: сетка уже построена; итерации по кривой депрессии."""
        bc_user = dict(bc_user or {})
        self.bc_list = [BoundaryCondition.from_dict(d) if isinstance(d, dict) else d
                        for d in (custom_bc or [])]
        if self._K_base is None:
            self._assemble_base()

        eps_h = self.cfg["eps_head"]
        eps_c = self.cfg["eps_curve"]
        relax = self.cfg["relax"]
        do_iter = self.cfg["depression_iterations"]

        curve = self._initial_depression(water_points, bc_user)
        prev_heads = None
        residual_c = float("inf")
        residual_h = float("inf")
        it = 0

        for it in range(1, self.cfg["max_iter"] + 1):
            fixed, summary = self._collect_dirichlet(water_points, bc_user,
                                                     curve if do_iter else None)
            heads = self._solve_heads(fixed)

            residual_h = float(np.max(np.abs(heads - prev_heads))) if prev_heads is not None else 1.0
            prev_heads = heads.copy()

            if not do_iter:
                residual_c = 0.0
                break

            # (2.1.12): |z_d,k − H_d,k| ≤ ε1 ; иначе (2.1.13) z = H
            new_curve, diffs = [], []
            xmin, _, xmax, _ = self.geom.bounds
            for (x, z) in curve:
                H = self._head_at_x(heads, x, z)
                diffs.append(abs(H - z))
                z_new = z + relax * (H - z)
                top = self.geom.surface_y(x)
                bot, _ = self.geom.get_slope_height(x)
                if top is not None:
                    z_new = min(max(z_new, (bot or 0.0) + 1e-3), top)
                new_curve.append((x, float(z_new)))
            residual_c = max(diffs) if diffs else 0.0
            curve = new_curve
            if residual_c <= eps_c and residual_h <= max(eps_h, 1e-4):
                break

        self.heads = heads
        self.bc_summary = summary
        self.depression = curve if do_iter else self._extract_depression(heads)
        self.iterations = it
        self.curve_residual = residual_c
        self.head_residual = residual_h
        self.converged = residual_c <= eps_c
        if not self.converged and do_iter:
            self.warnings.append(
                f"Кривая депрессии не сошлась за {it} итераций "
                f"(невязка {residual_c:.3f} м > ε1={eps_c}). "
                f"Увеличьте число итераций или плотность сетки.")
        self.calculate_darcy_vectors()
        return self.heads

    def _head_at_x(self, heads, x, z_hint):
        """Напор вблизи вертикали x на высоте ~z_hint (интерполяция по узлам)."""
        xmin, _, xmax, _ = self.geom.bounds
        dx = (xmax - xmin) / 60.0
        idx = np.where(np.abs(self.nodes[:, 0] - x) < dx)[0]
        if len(idx) == 0:
            return z_hint
        w = 1.0 / (np.abs(self.nodes[idx, 1] - z_hint) + 0.05)
        return float(np.average(heads[idx], weights=w))

    def _extract_depression(self, heads):
        xmin, ymin, xmax, ymax = self.geom.bounds
        out = []
        for x in np.linspace(xmin, xmax, 60):
            idx = np.where(np.abs(self.nodes[:, 0] - x) < (xmax - xmin) / 50.0)[0]
            best = None
            for i in idx:
                y, H = self.nodes[i, 1], heads[i]
                if abs(H - y) < 0.35 and (best is None or y > best):
                    best = y
            if best is not None:
                out.append((float(x), float(best)))
        return out

    # ============================================== 4. ПОСТПРОЦЕССИНГ
    def calculate_darcy_vectors(self):
        if self.heads is None:
            raise ValueError("Сначала выполните solve()")
        h = self.heads[self.elements]
        good = self._area > 1e-9
        dHdx = np.zeros(len(self.elements))
        dHdy = np.zeros(len(self.elements))
        dHdx[good] = np.sum(h[good] * self._b[good], axis=1) / (2 * self._area[good])
        dHdy[good] = np.sum(h[good] * self._c[good], axis=1) / (2 * self._area[good])
        kxx, kzz, kxz = self.element_k[:, 0], self.element_k[:, 1], self.element_k[:, 2]
        vx = -(kxx * dHdx + kxz * dHdy)
        vz = -(kxz * dHdx + kzz * dHdy)
        self.velocities = np.column_stack([vx, vz])
        return self.velocities

    def element_centers(self):
        return self.nodes[self.elements].mean(axis=1)

    def equipotentials(self, levels=9):
        if self.heads is None:
            return []
        hmin, hmax = float(self.heads.min()), float(self.heads.max())
        if hmax - hmin < 1e-9:
            return []
        out = []
        for lev in np.linspace(hmin, hmax, levels + 2)[1:-1]:
            segs = []
            for e in self.elements:
                pts = []
                for a, b in ((0, 1), (1, 2), (2, 0)):
                    ha, hb = self.heads[e[a]], self.heads[e[b]]
                    if (ha - lev) * (hb - lev) < 0:
                        t = (lev - ha) / (hb - ha)
                        pa, pb = self.nodes[e[a]], self.nodes[e[b]]
                        pts.append(tuple(pa + t * (pb - pa)))
                if len(pts) == 2:
                    segs.append((pts[0], pts[1], float(lev)))
            if segs:
                out.append((float(lev), segs))
        return out

    def streamlines(self, n_seeds=9, max_steps=600):
        """Линии тока: интегрирование v. При k_x≠k_z НЕ ортогональны изолиниям."""
        if self.velocities is None:
            self.calculate_darcy_vectors()
        centers = self.element_centers()
        if np.linalg.norm(self.velocities, axis=1).max() <= 0:
            return []
        xmin, ymin, xmax, ymax = self.geom.bounds
        span = max(ymax - ymin, 1e-6)
        seeds = [(xmin + 0.015 * (xmax - xmin), ymin + span * (i + 1) / (n_seeds + 1))
                 for i in range(n_seeds)]
        hstep = 0.02 * min(xmax - xmin, ymax - ymin)
        lines = []
        for s in seeds:
            p = np.array(s, float)
            line = [tuple(p)]
            for _ in range(max_steps):
                j = int(np.argmin(np.linalg.norm(centers - p, axis=1)))
                v = self.velocities[j]
                nv = np.linalg.norm(v)
                if nv < 1e-14:
                    break
                p = p + v / nv * hstep
                if not is_point_in_poly(tuple(p), self.geom.points):
                    break
                line.append(tuple(p))
            if len(line) > 4:
                lines.append(line)
        return lines

    def boundary_discharges(self):
        """Q = Σ (v·n)·Δl по границе, м²/сут на 1 м длины. Контроль баланса."""
        if self.velocities is None:
            self.calculate_darcy_vectors()
        xmin, ymin, xmax, ymax = self.geom.bounds
        centers = self.element_centers()
        speeds = np.linalg.norm(self.velocities, axis=1)
        tol = max(1e-6, (xmax - xmin) * 0.02)
        q = {"left": 0.0, "right": 0.0, "bottom": 0.0, "top": 0.0}
        # интегрируем по граничным рёбрам треугольников
        edge_count = {}
        for ei, e in enumerate(self.elements):
            for a, b in ((0, 1), (1, 2), (2, 0)):
                key = tuple(sorted((int(e[a]), int(e[b]))))
                edge_count[key] = edge_count.get(key, 0) + 1
        for (i, j), cnt in edge_count.items():
            if cnt != 1:
                continue                      # внутреннее ребро
            p1, p2 = self.nodes[i], self.nodes[j]
            mid = (p1 + p2) / 2.0
            L = float(np.linalg.norm(p2 - p1))
            ei = int(np.argmin(np.linalg.norm(centers - mid, axis=1)))
            v = self.velocities[ei]
            # внешняя нормаль
            n = np.array([p2[1] - p1[1], -(p2[0] - p1[0])])
            nn = np.linalg.norm(n)
            if nn < 1e-12:
                continue
            n /= nn
            third = [k for k in range(3)
                     if int(self.elements[ei][k]) not in (i, j)]
            if third:
                inner = self.nodes[self.elements[ei][third[0]]]
                if np.dot(n, inner - mid) > 0:
                    n = -n
            flux = float(np.dot(v, n)) * L
            if abs(mid[0] - xmin) <= tol:
                q["left"] += flux
            elif abs(mid[0] - xmax) <= tol:
                q["right"] += flux
            elif abs(mid[1] - ymin) <= tol:
                q["bottom"] += flux
            else:
                q["top"] += flux
        q["imbalance"] = q["left"] + q["right"] + q["bottom"] + q["top"]
        q["through"] = abs(q["left"])
        if abs(q["bottom"]) > 0.02 * max(abs(q["left"]), 1e-9):
            self.warnings.append("Заметный поток через низ — проверьте непроницаемость основания.")
        return q

    def point_discharges(self, nx=12, ny=8):
        """Расход (скорость Дарси) в характерных точках — для таблицы/CSV."""
        if self.velocities is None:
            self.calculate_darcy_vectors()
        centers = self.element_centers()
        xmin, ymin, xmax, ymax = self.geom.bounds
        rows = []
        for i in range(nx):
            x = xmin + (xmax - xmin) * (i + 0.5) / nx
            for j in range(ny):
                y = ymin + (ymax - ymin) * (j + 0.5) / ny
                if not is_point_in_poly((x, y), self.geom.points):
                    continue
                ei = int(np.argmin(np.linalg.norm(centers - [x, y], axis=1)))
                vx, vy = self.velocities[ei]
                kxx, kzz, kxz = self.element_k[ei]
                rows.append({"x": x, "y": y,
                             "H": float(self._head_at_x(self.heads, x, y)),
                             "vx": float(vx), "vy": float(vy),
                             "v": float(math.hypot(vx, vy)),
                             "k_x": float(kxx), "k_z": float(kzz)})
        return rows

    def seepage_face(self):
        """Участок высачивания: где на выходной грани H ≥ z."""
        if self.heads is None:
            return []
        xmin, ymin, xmax, ymax = self.geom.bounds
        tol = max(1e-6, (xmax - xmin) * 0.02)
        pts = [(float(self.nodes[i, 1]), float(self.heads[i]))
               for i in range(len(self.nodes)) if abs(self.nodes[i, 0] - xmax) <= tol]
        pts.sort()
        segs, cur = [], []
        for y, H in pts:
            if H >= y - 1e-6:
                cur.append((xmax, y))
            else:
                if len(cur) > 1:
                    segs.append(cur)
                cur = []
        if len(cur) > 1:
            segs.append(cur)
        return segs


class FiltrationCalculator:
    """Фасад для GUI: один вызов solve() -> FiltrationResult."""

    def __init__(self, points, layers=None, points_per_meter=None, settings=None):
        self.solver = FEMFiltrationSolver(settings)
        self.points = points
        self.layers = layers
        self.ppm = points_per_meter
        self.error = None

    def solve(self, water_points=None, bc=None, custom_bc=None, **kwargs):
        try:
            self.solver.generate_mesh(self.points, self.layers, self.ppm)
            self.solver.solve(water_points, bc, custom_bc)
            s = self.solver
            res = FiltrationResult(
                nodes=s.nodes, elements=s.elements, heads=s.heads,
                velocities=s.velocities, centers=s.element_centers(),
                speeds=np.linalg.norm(s.velocities, axis=1),
                depression=s.depression,
                equipotentials=s.equipotentials(levels=9),
                streamlines=s.streamlines(n_seeds=9),
                seepage_segments=s.seepage_face(),
                discharge=s.boundary_discharges(),
                iterations=s.iterations, curve_residual=s.curve_residual,
                head_residual=s.head_residual, converged=s.converged,
                bc_summary=s.bc_summary, warnings=list(s.warnings),
                point_discharges=s.point_discharges())
            return res
        except Exception as exc:            # noqa: BLE001
            self.error = str(exc)
            return None