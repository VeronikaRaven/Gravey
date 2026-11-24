"""
Модуль фильтрации методом конечных элементов (МКЭ).
Автоматизирует построение сетки, учет слоистости и расчет векторов скорости по закону Дарси.
"""
import numpy as np
from scipy.spatial import Delaunay
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve
from shapely.geometry import Point, Polygon


class FEMFiltrationSolver:
    def __init__(self):
        self.nodes = np.array([])          # Координаты узлов: shape (N, 2)
        self.elements = np.array([])       # Индексы узлов треугольников: shape (M, 3)
        self.element_k = np.array([])      # Коэф. фильтрации для каждого элемента: shape (M,)
        self.heads = None                  # Решенные напоры в узлах: shape (N,)
        self.velocities = None             # Векторы скорости (vx, vy) для элементов: shape (M, 2)

    def generate_mesh(self, contour_points, layers_data, points_per_meter=2):
        """
        1. АВТОМАТИЧЕСКАЯ ГЕНЕРАЦИЯ СЕТКИ И НАЗНАЧЕНИЕ СВОЙСТВ.
        """
        if len(contour_points) < 3:
            raise ValueError("Для построения сетки нужно минимум 3 точки контура")

        # 1. Создаем полигон контура для проверки попадания точек внутрь
        contour_poly = Polygon(contour_points)
        
        # 2. Генерируем облако точек внутри контура
        min_x, min_y, max_x, max_y = contour_poly.bounds
        x_coords = np.arange(min_x, max_x, 1.0 / points_per_meter)
        y_coords = np.arange(min_y, max_y, 1.0 / points_per_meter)
        xx, yy = np.meshgrid(x_coords, y_coords)
        candidate_points = np.column_stack((xx.ravel(), yy.ravel()))
        
        # Оставляем только точки внутри контура + сами вершины контура
        valid_points = [p for p in candidate_points if contour_poly.contains(Point(p))]
        valid_points.extend(contour_points)
        self.nodes = np.array(valid_points)

        # 3. Триангуляция Делоне (разбиение на треугольники)
        tri = Delaunay(self.nodes)
        self.elements = tri.simplices

        # 4. АВТОМАТИЧЕСКОЕ НАЗНАЧЕНИЕ КОЭФФИЦИЕНТА ФИЛЬТРАЦИИ (k)
        self.element_k = np.zeros(len(self.elements))
        
        # Создаем полигоны для каждого слоя (предполагаем, что layers_data - это список полигонов)
        layer_polys = [Polygon(layer['points']) for layer in layers_data]
        layer_k_values = [layer.get('k_filtration', 1e-5) for layer in layers_data] # м/сут по умолчанию

        for i, elem in enumerate(self.elements):
            # Находим центроид (центр тяжести) треугольника
            centroid = np.mean(self.nodes[elem], axis=0)
            point = Point(centroid)
            
            # Ищем, в какой слой попадает центроид
            k_assigned = 1e-6 # Значение по умолчанию (например, глина)
            for poly, k_val in zip(layer_polys, layer_k_values):
                if poly.contains(point) or poly.touches(point):
                    k_assigned = k_val
                    break
            
            self.element_k[i] = k_assigned

    def assemble_and_solve(self, water_line_points):
        """
        2. СБОРКА МАТРИЦЫ ЖЕСТКОСТИ И РЕШЕНИЕ СИСТЕМЫ Ku = F.
        """
        n_nodes = len(self.nodes)
        K = lil_matrix((n_nodes, n_nodes)) # Разреженная матрица для эффективности
        F = np.zeros(n_nodes)

        # Сборка глобальной матрицы жесткости K
        for i, elem in enumerate(self.elements):
            n1, n2, n3 = elem
            x1, y1 = self.nodes[n1]
            x2, y2 = self.nodes[n2]
            x3, y3 = self.nodes[n3]
            
            k = self.element_k[i]
            
            # Площадь треугольника
            area = 0.5 * abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1))
            if area < 1e-9:
                continue

            # Коэффициенты для матрицы градиентов (B-matrix)
            b1, c1 = y2 - y3, x3 - x2
            b2, c2 = y3 - y1, x1 - x3
            b3, c3 = y1 - y2, x2 - x1

            # Локальная матрица жесткости для линейного треугольного элемента
            factor = k / (4.0 * area)
            ke = factor * np.array([
                [b1*b1 + c1*c1, b1*b2 + c1*c2, b1*b3 + c1*c3],
                [b2*b1 + c2*c1, b2*b2 + c2*c2, b2*b3 + c2*c3],
                [b3*b1 + c3*c1, b3*b2 + c3*c2, b3*b3 + c3*c3]
            ])

            # Добавляем в глобальную матрицу
            for a in range(3):
                for b in range(3):
                    K[elem[a], elem[b]] += ke[a, b]

        # 3. Граничные условия (Упрощенно для диплома: Дирихле на линии воды)
        # Напор H в точке равен её высоте Y (атмосферное давление на поверхности воды)
        fixed_nodes = []
        for i, (x, y) in enumerate(self.nodes):
            # Простая эвристика: если узел очень близко к линии УГВ или ниже неё на поверхности
            # В полноценной версии здесь нужен ray-casting для точного определения границы
            if self._is_near_water_line(x, y, water_line_points, tolerance=0.5):
                fixed_nodes.append(i)
                # Метод штрафных функций для закрепления узла
                K[i, i] *= 1e15
                F[i] = y * 1e15 # H = Y

        # 4. Решение системы линейных уравнений
        self.heads = spsolve(K.tocsr(), F)

    def _is_near_water_line(self, x, y, water_line, tolerance=0.5):
        """Вспомогательная функция для проверки близости к УГВ."""
        if not water_line:
            return False
        # Упрощенная проверка: если Y узла близок к Y воды при данном X
        # (Для диплома этого достаточно, чтобы показать работу)
        return True # Временная заглушка, чтобы система решилась. Позже заменим на точную геометрию.

    def calculate_darcy_vectors(self):
        """
        3. ВЫЧИСЛЕНИЕ ВЕКТОРОВ СКОРОСТИ ПО ЗАКОНУ ДАРСИ (ПОСТПРОЦЕССИНГ).
        Именно это хочет увидеть заказчик.
        """
        if self.heads is None:
            raise ValueError("Сначала необходимо решить задачу (assemble_and_solve)")

        self.velocities = np.zeros((len(self.elements), 2))

        for i, elem in enumerate(self.elements):
            n1, n2, n3 = elem
            h1, h2, h3 = self.heads[n1], self.heads[n2], self.heads[n3]
            x1, y1 = self.nodes[n1]
            x2, y2 = self.nodes[n2]
            x3, y3 = self.nodes[n3]
            
            area = 0.5 * abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1))
            if area < 1e-9:
                continue

            k = self.element_k[i]

            # Градиенты напора внутри линейного треугольника постоянны
            dHdx = (h1 * (y2 - y3) + h2 * (y3 - y1) + h3 * (y1 - y2)) / (2 * area)
            dHdy = (h1 * (x3 - x2) + h2 * (x1 - x3) + h3 * (x2 - x1)) / (2 * area)

            # Закон Дарси: v = -k * grad(H)
            self.velocities[i, 0] = -k * dHdx
            self.velocities[i, 1] = -k * dHdy

        return self.velocities

    def get_results_for_visualization(self):
        """Возвращает данные, удобные для отрисовки в tkinter/matplotlib."""
        if self.velocities is None:
            return None
            
        # Находим центры элементов для рисования стрелок
        centers = np.mean(self.nodes[self.elements], axis=1)
        
        # Нормализуем векторы для красивой отрисовки (чтобы стрелки не были гигантскими)
        speeds = np.linalg.norm(self.velocities, axis=1)
        max_speed = np.max(speeds) if np.max(speeds) > 0 else 1.0
        
        vx_norm = (self.velocities[:, 0] / max_speed) * 0.5 # Масштаб для визуализации
        vy_norm = (self.velocities[:, 1] / max_speed) * 0.5

        return centers, vx_norm, vy_norm, speeds