# calculator.py
import math
from config import SAFETY_FACTORS, SOIL_PRESETS


class SlopeCalculator:
    def __init__(self, points, water_points, obj_type="permanent", layers=None, soil_props=None):
        self.points = points
        self.water_points = water_points
        self.obj_type = obj_type
        self.required_fs = SAFETY_FACTORS[obj_type]
        self.layers = layers if layers else []

        if soil_props:
            self.default_soil = soil_props
        else:
            self.default_soil = SOIL_PRESETS["Суглинок"].copy()

    def get_water_y(self, x):
        if not self.water_points: return -999.0
        for i in range(len(self.water_points) - 1):
            x1, y1 = self.water_points[i]
            x2, y2 = self.water_points[i + 1]
            if min(x1, x2) <= x <= max(x1, x2):
                if x2 == x1: return y1
                t = (x - x1) / (x2 - x1)
                return y1 + t * (y2 - y1)
        return -999.0

    def get_soil_props_at_y(self, y):
        """Возвращает свойства грунта для заданной высоты (Y) с учетом слоев"""
        if not self.layers:
            return self.default_soil['gamma'], self.default_soil['c'], self.default_soil['phi']

        for layer in self.layers:
            if layer['y_bottom'] <= y <= layer['y_top']:
                return layer['gamma'], layer['c'], layer['phi']

        # Вне дополнительных слоев остается основной грунт.
        return self.default_soil['gamma'], self.default_soil['c'], self.default_soil['phi']

    def get_slope_height(self, x):
        intersections = []
        n = len(self.points)
        for i in range(n):
            x1, y1 = self.points[i]
            x2, y2 = self.points[(i + 1) % n]
            if min(x1, x2) <= x <= max(x1, x2):
                if abs(x2 - x1) > 1e-6:
                    t = (x - x1) / (x2 - x1)
                    y = y1 + t * (y2 - y1)
                    intersections.append(y)
        if not intersections: return None, None
        intersections.sort()
        return intersections[0], intersections[-1]

    def calculate_bishop(self, num_slices=30):
        if len(self.points) < 3: return []

        x_coords = [p[0] for p in self.points]
        y_coords = [p[1] for p in self.points]
        xmin, xmax = min(x_coords), max(x_coords)
        ymin, ymax = min(y_coords), max(y_coords)

        width = xmax - xmin
        height = ymax - ymin

        circles = []

        cx_list = [xmin + width * i / 10.0 for i in range(11)]
        cy_list = [ymax + height * 0.1 + height * 1.5 * j / 10.0 for j in range(11)]

        for cx in cx_list:
            for cy in cy_list:
                r_min = min(math.hypot(cx - p[0], cy - p[1]) for p in self.points)
                r_max = max(math.hypot(cx - p[0], cy - p[1]) for p in self.points)

                for k in range(1, 6):
                    r = r_min + (r_max - r_min) * k / 5.0
                    fs = self._calculate_fs_for_circle(cx, cy, r, num_slices)

                    if fs and 0.1 < fs < 5.0:
                        circles.append((fs, cx, cy, r))

        circles.sort(key=lambda x: x[0])

        unique = []
        for c in circles:
            if not any(math.hypot(c[1] - u[1], c[2] - u[2]) < width * 0.1 for u in unique):
                unique.append(c)
            if len(unique) >= 3:
                break
        return unique

    def _calculate_fs_for_circle(self, cx, cy, r, n_slices):
        x_coords = [p[0] for p in self.points]
        xmin, xmax = min(x_coords), max(x_coords)

        dx = (xmax - xmin) / n_slices
        sum_Ti = 0.0
        sum_Ei = 0.0

        for i in range(n_slices):
            x_mid = xmin + (i + 0.5) * dx
            y_bot, y_top = self.get_slope_height(x_mid)
            if y_bot is None: continue

            under_sqrt = r ** 2 - (x_mid - cx) ** 2
            if under_sqrt < 0: continue

            y_circle = cy - math.sqrt(under_sqrt)
            if y_circle > y_top: continue

            h = y_top - max(y_bot, y_circle)
            if h <= 0: continue

            sin_alpha = (x_mid - cx) / r
            cos_alpha = math.sqrt(max(0, 1 - sin_alpha ** 2))

            # Берем свойства СРЕДНЕГО слоя для отсека
            y_mid_slice = (y_circle + y_top) / 2
            gamma, c_dry, phi_dry = self.get_soil_props_at_y(y_mid_slice)

            water_y = self.get_water_y(x_mid)
            if water_y != -999 and y_circle < water_y:
                gamma = gamma - 9.81
                c = c_dry * 0.6
                phi = phi_dry * 0.8
            else:
                c = c_dry
                phi = phi_dry

            P = gamma * h * dx
            N = P * cos_alpha
            E = P * sin_alpha

            l = dx / cos_alpha if cos_alpha > 0.01 else dx
            T = N * math.tan(math.radians(phi)) + c * l

            sum_Ti += T
            sum_Ei += abs(E)

        if sum_Ei == 0: return None
        return sum_Ti / sum_Ei
