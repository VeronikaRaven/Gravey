# thermal.py
import math


class ThermalCalculator:
    def __init__(self, points):
        self.points = points

    def solve(self, T_air=-20, T_init=5, days=30, delta_x=1.0):
        if len(self.points) < 3: return None

        x_coords = [p[0] for p in self.points]
        y_coords = [p[1] for p in self.points]
        xmin, xmax = min(x_coords), max(x_coords)
        ymin, ymax = min(y_coords), max(y_coords)

        nx = int((xmax - xmin) / delta_x) + 1
        ny = int((ymax - ymin) / delta_x) + 1

        lam_M, lam_T = 1.8, 1.2
        C_M, C_T = 1.8e6, 2.2e6
        Q_lat_val = 335000 * 1800 * 0.2

        a_max = max(lam_M / C_M, lam_T / C_T)
        dt = 0.8 * (delta_x ** 2) / (4 * a_max)
        total_time = days * 24 * 3600
        steps = int(total_time / dt)
        if steps > 5000: steps = 5000  # Ограничитель для скорости

        T = [[T_init for j in range(ny)] for i in range(nx)]
        Q_acc = [[0.0 for j in range(ny)] for i in range(nx)]

        def is_inside(x, y):
            inside = False
            n = len(self.points)
            for i in range(n):
                x1, y1 = self.points[i]
                x2, y2 = self.points[(i + 1) % n]
                if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
                    inside = not inside
            return inside

        active = [[False] * ny for _ in range(nx)]
        for i in range(nx):
            for j in range(ny):
                if is_inside(xmin + i * delta_x, ymin + j * delta_x):
                    active[i][j] = True

        for step in range(steps):
            T_new = [row[:] for row in T]
            for i in range(1, nx - 1):
                for j in range(1, ny - 1):
                    if not active[i][j]: continue

                    # Условия на границах
                    if not active[i][j + 1] or not active[i + 1][j] or not active[i - 1][j]:
                        # Контакт с воздухом (верх и бока)
                        alpha_conv = 20.0
                        dQ = alpha_conv * (T_air - T[i][j]) * delta_x * dt
                        T_new[i][j] += dQ / (C_T * delta_x ** 2)
                        continue
                    elif not active[i][j - 1]:
                        # Контакт с теплым основанием (низ)
                        T_new[i][j] = T_init
                        continue

                    if T[i][j] < 0:
                        lam, C = lam_M, C_M
                    else:
                        lam, C = lam_T, C_T

                    dT = (lam * dt) / (C * delta_x ** 2) * (
                            T[i + 1][j] + T[i - 1][j] + T[i][j + 1] + T[i][j - 1] - 4 * T[i][j]
                    )

                    T_old = T[i][j]
                    T_mid = T_old + dT

                    # Фазовый переход
                    if T_old > 0 and T_mid <= 0:
                        Q_cool = T_old * C_T + abs(T_mid) * C_M
                        if Q_cool >= Q_lat_val:
                            T_new[i][j] = - (Q_cool - Q_lat_val) / C_M
                        else:
                            T_new[i][j] = 0
                    elif T_old < 0 and T_mid >= 0:
                        Q_heat = abs(T_old) * C_M + T_mid * C_T
                        if Q_heat >= Q_lat_val:
                            T_new[i][j] = (Q_heat - Q_lat_val) / C_T
                        else:
                            T_new[i][j] = 0
                    elif T_old == 0:
                        if dT > 0:
                            Q_acc[i][j] += dT * C_T
                            if Q_acc[i][j] >= Q_lat_val:
                                T_new[i][j] = (Q_acc[i][j] - Q_lat_val) / C_T
                                Q_acc[i][j] = 0
                            else:
                                T_new[i][j] = 0
                        else:
                            Q_acc[i][j] += dT * C_M
                            if abs(Q_acc[i][j]) >= Q_lat_val:
                                T_new[i][j] = (Q_acc[i][j] + Q_lat_val) / C_M
                                Q_acc[i][j] = 0
                            else:
                                T_new[i][j] = 0
                    else:
                        T_new[i][j] = T_mid

            T = T_new

        # Формирование линии 0 градусов (СТРОГО СВЕРХУ ВНИЗ, БЕЗ ЗИГЗАГОВ)
        freeze_curve = []
        for i in range(nx):
            x_m = xmin + i * delta_x
            for j in range(ny - 1, 0, -1):
                if active[i][j] and active[i][j - 1]:
                    if (T[i][j] <= 0) and (T[i][j - 1] > 0):
                        frac = abs(T[i][j]) / (abs(T[i][j]) + abs(T[i][j - 1]))
                        y_m = ymin + (j - frac) * delta_x
                        freeze_curve.append((x_m, y_m))
                        break  # Берем только первую границу сверху и выходим из цикла!

        freeze_curve.sort(key=lambda p: p[0])
        return freeze_curve