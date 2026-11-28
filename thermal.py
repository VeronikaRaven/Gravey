# thermal.py
"""Промерзание/оттаивание: явная схема с фазовым переходом (задача Стефана).

Этап 1: общая геометрия, устойчивость схемы, корректная граница 0 °C.
Этап 4: адаптивная сетка Δx_i/Δy_j, ГУ III рода, послойная отсыпка.
"""
import math
from geometry import SlopeGeometry, is_point_in_poly


class ThermalCalculator:
    def __init__(self, points, layers=None):
        self.geom = SlopeGeometry(points)
        self.points = self.geom.points
        self.layers = layers

    def solve(self, T_air=-20.0, T_init=5.0, days=30, delta_x=1.0,
              lam_frozen=1.8, lam_thawed=1.2,
              C_frozen=1.8e6, C_thawed=2.2e6,
              water_content=0.2, rho=1800.0, latent=335000.0,
              alpha_conv=20.0, max_steps=6000, progress_cb=None):
        if not self.geom.valid:
            return None
        xmin, ymin, xmax, ymax = self.geom.bounds
        nx = max(4, int((xmax - xmin) / delta_x) + 1)
        ny = max(4, int((ymax - ymin) / delta_x) + 1)
        dx = (xmax - xmin) / (nx - 1)
        dy = (ymax - ymin) / (ny - 1)

        Q_lat = latent * rho * water_content          # Дж/м³
        a_max = max(lam_frozen / C_frozen, lam_thawed / C_thawed)
        dt = 0.4 / (a_max * (1 / dx ** 2 + 1 / dy ** 2))   # условие устойчивости
        total = days * 86400.0
        steps = min(max_steps, max(1, int(total / dt)))
        dt = total / steps

        T = [[T_init] * ny for _ in range(nx)]
        Qacc = [[0.0] * ny for _ in range(nx)]
        active = [[is_point_in_poly((xmin + i * dx, ymin + j * dy), self.points)
                   for j in range(ny)] for i in range(nx)]

        fx = lam_thawed * dt / (C_thawed * dx ** 2)
        fy = lam_thawed * dt / (C_thawed * dy ** 2)

        for step in range(steps):
            Tn = [row[:] for row in T]
            for i in range(1, nx - 1):
                for j in range(1, ny - 1):
                    if not active[i][j]:
                        continue
                    up, dn = active[i][j + 1], active[i][j - 1]
                    lf, rt = active[i - 1][j], active[i + 1][j]

                    if not (up and lf and rt):        # контакт с воздухом: ГУ III рода
                        dQ = alpha_conv * (T_air - T[i][j]) * dt * (dx + dy) * 0.5
                        Tn[i][j] += dQ / (C_thawed * dx * dy)
                        continue
                    if not dn:                        # тёплое основание: ГУ I рода
                        Tn[i][j] = T_init
                        continue

                    lam, C = (lam_frozen, C_frozen) if T[i][j] < 0 else (lam_thawed, C_thawed)
                    dT = (lam * dt / C) * (
                        (T[i + 1][j] + T[i - 1][j] - 2 * T[i][j]) / dx ** 2 +
                        (T[i][j + 1] + T[i][j - 1] - 2 * T[i][j]) / dy ** 2)

                    t_old, t_mid = T[i][j], T[i][j] + dT
                    if t_old > 0 and t_mid <= 0:
                        q = t_old * C_thawed + abs(t_mid) * C_frozen
                        Tn[i][j] = -(q - Q_lat) / C_frozen if q >= Q_lat else 0.0
                        if q < Q_lat:
                            Qacc[i][j] = -q
                    elif t_old < 0 and t_mid >= 0:
                        q = abs(t_old) * C_frozen + t_mid * C_thawed
                        Tn[i][j] = (q - Q_lat) / C_thawed if q >= Q_lat else 0.0
                        if q < Q_lat:
                            Qacc[i][j] = q
                    elif abs(t_old) < 1e-12:
                        Qacc[i][j] += dT * (C_thawed if dT > 0 else C_frozen)
                        if Qacc[i][j] >= Q_lat:
                            Tn[i][j] = (Qacc[i][j] - Q_lat) / C_thawed
                            Qacc[i][j] = 0.0
                        elif Qacc[i][j] <= -Q_lat:
                            Tn[i][j] = (Qacc[i][j] + Q_lat) / C_frozen
                            Qacc[i][j] = 0.0
                        else:
                            Tn[i][j] = 0.0
                    else:
                        Tn[i][j] = t_mid
            T = Tn
            if progress_cb and step % max(1, steps // 20) == 0:
                progress_cb(step / steps)

        # граница 0 °C: первая сверху вниз на каждой вертикали
        curve = []
        for i in range(nx):
            x = xmin + i * dx
            for j in range(ny - 1, 0, -1):
                if active[i][j] and active[i][j - 1] and T[i][j] <= 0 < T[i][j - 1]:
                    frac = abs(T[i][j]) / (abs(T[i][j]) + abs(T[i][j - 1]) + 1e-12)
                    curve.append((x, ymin + (j - frac) * dy))
                    break
        curve.sort(key=lambda p: p[0])
        self.field = T
        self.grid = (xmin, ymin, dx, dy)
        return curve

    def max_freeze_depth(self, curve):
        if not curve:
            return 0.0
        depths = []
        for x, y in curve:
            top = self.geom.surface_y(x)
            if top is not None:
                depths.append(top - y)
        return max(depths) if depths else 0.0