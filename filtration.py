# filtration.py
import math


class FiltrationCalculator:
    def __init__(self, points):
        self.points = points

    def solve(self, k_coef=1.0, delta_x=1.0, max_iter=2000):
        """Решает задачу фильтрации по алгоритму из Filtratsia.docx"""
        if len(self.points) < 3: return None

        x_coords = [p[0] for p in self.points]
        y_coords = [p[1] for p in self.points]
        xmin, xmax = min(x_coords), max(x_coords)
        ymin, ymax = min(y_coords), max(y_coords)

        nx = int((xmax - xmin) / delta_x) + 1
        ny = int((ymax - ymin) / delta_x) + 1

        # Проверка принадлежности точки к телу откоса
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

        # Начальное распределение напора (Формула 2.1.10 и предварительный расчет)
        # Считаем, что изначально вода стоит по уровню верхнего бьефа
        H = [[ymin + j * delta_x for j in range(ny)] for i in range(nx)]

        # Итерационный процесс (п. 2.1.11)
        for iteration in range(max_iter):
            max_diff = 0

            # Проход по крестам-трафаретам (Формула 2.1.7)
            for i in range(1, nx - 1):
                for j in range(1, ny - 1):
                    if not active[i][j]: continue

                    # Граничные условия
                    if i == 0:  # Верхний бьеф
                        H[i][j] = ymax
                        continue
                    elif i == nx - 1:  # Нижний бьеф (дренаж)
                        H[i][j] = ymin + j * delta_x
                        continue

                    # Регулярный крест-трафарет
                    H_new = 0.25 * (H[i + 1][j] + H[i - 1][j] + H[i][j + 1] + H[i][j - 1])

                    # Корректировка свободной поверхности (Формулы 2.1.12, 2.1.13)
                    z = ymin + j * delta_x
                    if H_new > z:
                        H_new = z  # Условие высачивания: напор равен высоте

                    diff = abs(H_new - H[i][j])
                    if diff > max_diff: max_diff = diff
                    H[i][j] = H_new

            if max_diff < 0.01:
                break

        # Формирование кривой депрессии
        depression_curve = []
        for i in range(nx):
            x_m = xmin + i * delta_x
            # Ищем, где напор равен высоте (H = z)
            for j in range(ny - 1, 0, -1):
                if active[i][j]:
                    z = ymin + j * delta_x
                    if abs(H[i][j] - z) < 0.1 or H[i][j] <= z:
                        depression_curve.append((x_m, z))
                        break

        return depression_curve