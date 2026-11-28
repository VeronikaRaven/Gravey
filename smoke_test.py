# smoke_test.py
"""Быстрая проверка расчётных модулей (запуск: python smoke_test.py)."""
from calculator import SlopeCalculator
from filtration import FiltrationCalculator
from thermal import ThermalCalculator
from layers import SoilLayer, LayerCollection
from project import Project
from history import HistoryManager

POINTS = [(0, 0), (0, 6), (8, 6), (20, 0), (0, 0)]
WATER = [(0, 4.0), (8, 3.5), (20, 0.5)]


def main():
    layers = LayerCollection([SoilLayer.from_preset("Песок", [(8, 6), (20, 0), (14, 0), (8, 3)])])

    calc = SlopeCalculator(POINTS, WATER, "permanent", layers,
                           {"gamma": 19.0, "c": 15.0, "phi": 18.0})
    res = calc.search_critical("bishop", n_slices=30)
    assert res, "Бишоп не нашёл поверхностей"
    print(f"[OK] Бишоп: FS = {res[0].fs:.3f} (норма {res[0].required_fs}), "
          f"срезов {len(res[0].slices)}, итераций {res[0].iterations}")

    f = FiltrationCalculator(POINTS, layers, points_per_meter=1.5)
    fr = f.solve(water_points=WATER, bc={"left_head": 4.0})
    assert fr, f"Фильтрация: {f.error}"
    print(f"[OK] Фильтрация: узлов {fr['nodes']}, элементов {fr['elements']}, "
          f"эквипотенциалей {len(fr['equipotentials'])}, линий тока {len(fr['streamlines'])}, "
          f"Q≈{fr['discharge']:.4g}")

    t = ThermalCalculator(POINTS)
    curve = t.solve(T_air=-20, days=10, delta_x=1.0)
    print(f"[OK] Промерзание: точек {len(curve or [])}, "
          f"глубина {t.max_freeze_depth(curve or []):.2f} м")

    pr = Project()
    pr.points, pr.water_points, pr.layers = POINTS, WATER, layers
    pr.save("_test_project.json")
    pr2 = Project.load("_test_project.json")
    assert len(pr2.points) == len(POINTS) and len(pr2.layers) == 1
    print("[OK] Проект: сохранение/загрузка JSON")

    h = HistoryManager()
    h.push(pr.get_state(), "test")
    assert h.can_undo() and h.undo(pr.get_state()) is not None and h.can_redo()
    print("[OK] История: undo/redo")
    print("\nЭтап 1 пройден.")


if __name__ == "__main__":
    main()