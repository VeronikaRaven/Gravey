# project.py
"""Сохранение/загрузка проекта в JSON (читаемый формат, версионируемый)."""
import json
import os
from datetime import datetime

from config import PROJECT_FORMAT_VERSION, APP_NAME, APP_VERSION
from layers import LayerCollection

UNTITLED = "Безымянный проект"


class Project:
    def __init__(self):
        self.path = None
        self.name = UNTITLED
        self.points = []
        self.water_points = []
        self.layers = LayerCollection()
        self.is_closed = False
        self.soil = {"gamma": 19.0, "c": 15.0, "phi": 18.0}
        self.obj_type = "permanent"
        self.method = "bishop"
        self.num_slices = 30
        self.search_settings = {"nx": 11, "ny": 11, "nr": 5}
        self.filtration_bc = {}
        self.thermal = {"t_air": -20.0, "days": 30, "t_init": 5.0}
        self.variants = []          # варианты конфигураций (этап 3/5)
        self.results = {}           # последние результаты расчётов

    # ------------------------------------------------------------ состояние
    def get_state(self):
        """Снимок для истории и для сохранения."""
        return {
            "points": [list(p) for p in self.points],
            "water_points": [list(p) for p in self.water_points],
            "layers": self.layers.to_list(),
            "is_closed": self.is_closed,
            "soil": dict(self.soil),
            "obj_type": self.obj_type,
            "method": self.method,
            "num_slices": self.num_slices,
            "search_settings": dict(self.search_settings),
            "filtration_bc": dict(self.filtration_bc),
            "thermal": dict(self.thermal),
        }

    def set_state(self, st):
        self.points = [tuple(p) for p in st.get("points", [])]
        self.water_points = [tuple(p) for p in st.get("water_points", [])]
        self.layers = LayerCollection.from_list(st.get("layers", []))
        self.is_closed = st.get("is_closed", False)
        self.soil = dict(st.get("soil", self.soil))
        self.obj_type = st.get("obj_type", "permanent")
        self.method = st.get("method", "bishop")
        self.num_slices = int(st.get("num_slices", 30))
        self.search_settings = dict(st.get("search_settings", self.search_settings))
        self.filtration_bc = dict(st.get("filtration_bc", {}))
        self.thermal = dict(st.get("thermal", self.thermal))

    # ------------------------------------------------------------------ I/O
    def save(self, path=None):
        path = path or self.path
        if not path:
            raise ValueError("Не указан путь сохранения")
        data = {
            "format_version": PROJECT_FORMAT_VERSION,
            "app": f"{APP_NAME} {APP_VERSION}",
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "name": self.name,
            "model": self.get_state(),
            "variants": self.variants,
            "results": self.results,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        return path

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pr = cls()
        if "model" in data:                       # формат v2
            pr.set_state(data["model"])
            pr.variants = data.get("variants", [])
            pr.results = data.get("results", {})
        else:                                     # обратная совместимость v1
            pr.set_state({
                "points": data.get("points", []),
                "water_points": data.get("water_points", []),
                "layers": data.get("layers", []),
                "is_closed": data.get("is_closed", False),
                "soil": {"gamma": float(data.get("gamma", 19.0)),
                         "c": float(data.get("c", 15.0)),
                         "phi": float(data.get("phi", 18.0))},
                "obj_type": data.get("obj_type", "permanent"),
                "thermal": {"t_air": float(data.get("t_air", -20)),
                            "days": int(data.get("days", 30)), "t_init": 5.0},
            })
        pr.path = path
        pr.name = data.get("name") or os.path.splitext(os.path.basename(path))[0]
        return pr