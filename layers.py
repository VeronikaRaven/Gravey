# layers.py
"""Слои грунта: полный набор физико-механических характеристик."""
from dataclasses import dataclass, field, asdict, fields
import itertools

from config import SOIL_PRESETS, COLORS, GAMMA_W
from geometry import is_point_in_poly, polygon_centroid

_id_counter = itertools.count(1)


@dataclass
class SoilLayer:
    name: str = "Суглинок"
    polygon: list = field(default_factory=list)
    color: str = "#FFB3BA"
    enabled: bool = True
    number: int = 0
    uid: int = field(default_factory=lambda: next(_id_counter))

    # прочностные и весовые
    gamma: float = 19.0          # кН/м³ (природная влажность)
    gamma_sat: float = 0.0       # 0 -> вычисляется
    c: float = 15.0              # кПа, сухое/природное сцепление
    c_wet: float = 0.0           # 0 -> c * k_c
    phi: float = 18.0            # град
    k_c: float = 0.6             # коэффициент снижения c при подтоплении
    k_phi: float = 0.8           # коэффициент снижения phi
    h_cap: float = 2.0           # высота капиллярного поднятия, м
    c_h_factor: float = 1.0      # анизотропия прочности c_h / c_v

    # фильтрация
    k_x: float = 0.05            # м/сут
    k_z: float = 0.02            # м/сут
    anisotropy_angle: float = 0.0  # угол залегания слоёв, град

    # деформационные (для МКЭ) и физические
    E: float = 15000.0           # кПа
    nu: float = 0.35
    porosity: float = 0.42
    moisture: float = 0.25
    rho_dry: float = 1.58        # т/м³
    rho_sat: float = 1.95        # т/м³

    # thermal (этап 4)
    lambda_frozen: float = 1.8
    lambda_thawed: float = 1.2
    C_frozen: float = 1.8e6
    C_thawed: float = 2.2e6
    placement_day: float = 0.0   # день отсыпки слоя (послойное возведение)
    placement_temp: float = 5.0

    use_preset: bool = False     # галочка «брать из справочника»

    # ------------------------------------------------------------- свойства
    @property
    def effective_gamma_sat(self):
        return self.gamma_sat if self.gamma_sat > 0 else self.gamma + 0.0

    @property
    def buoyant_gamma(self):
        """Взвешенный удельный вес γ' = γ_sat − γ_w."""
        return max(1.0, self.effective_gamma_sat - GAMMA_W)

    @property
    def effective_c_wet(self):
        return self.c_wet if self.c_wet > 0 else self.c * self.k_c

    def strength(self, submerged=False, capillary_ratio=0.0):
        """(gamma, c, phi) с учётом подтопления и капиллярной зоны.

        capillary_ratio in [0..1] — доля капиллярного насыщения точки.
        Капиллярное давление отрицательное -> повышает эффективные напряжения.
        """
        if submerged:
            return self.buoyant_gamma, self.effective_c_wet, self.phi * self.k_phi
        if capillary_ratio > 0:
            g = self.gamma + (self.effective_gamma_sat - self.gamma) * capillary_ratio
            return g, self.c, self.phi
        return self.gamma, self.c, self.phi

    def capillary_pressure(self, height_above_wt):
        """u_cap ≤ 0 по линейному закону от −γw·h_k до 0 (Ustoychivost.docx)."""
        if height_above_wt is None or height_above_wt < 0 or self.h_cap <= 0:
            return 0.0
        if height_above_wt > self.h_cap:
            return 0.0
        return -GAMMA_W * (self.h_cap - height_above_wt)

    def k_tensor(self):
        """Тензор проницаемости с учётом угла залегания α (для этапа 2)."""
        import math
        a = math.radians(self.anisotropy_angle)
        ca, sa = math.cos(a), math.sin(a)
        kxx = self.k_x * ca * ca + self.k_z * sa * sa
        kzz = self.k_x * sa * sa + self.k_z * ca * ca
        kxz = (self.k_x - self.k_z) * ca * sa
        return kxx, kzz, kxz

    def contains(self, pt):
        return is_point_in_poly(pt, self.polygon)

    @property
    def centroid(self):
        return polygon_centroid(self.polygon)

    # ------------------------------------------------------- сериализация
    def to_dict(self):
        d = asdict(self)
        d["polygon"] = [list(p) for p in self.polygon]
        return d

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in d.items() if k in known}
        kw["polygon"] = [tuple(p) for p in kw.get("polygon", [])]
        if "uid" in kw:                      # сохраняем уникальность
            global _id_counter
            try:
                uid = int(kw["uid"])
                _id_counter = itertools.count(max(uid + 1, next(_id_counter) + 1))
                kw["uid"] = uid
            except (ValueError, TypeError):
                kw.pop("uid")
        return cls(**kw)

    @classmethod
    def from_preset(cls, name, polygon=None, number=0):
        p = SOIL_PRESETS.get(name, SOIL_PRESETS["Суглинок"])
        obj = cls(name=name, polygon=list(polygon or []), number=number,
                  color=p.get("color", "#FFB3BA"))
        for k, v in p.items():
            if hasattr(obj, k) and k not in ("color",):
                setattr(obj, k, v)
        obj.c_wet = p.get("c_wet", obj.c * obj.k_c)
        return obj


class LayerCollection:
    """Коллекция слоёв: порядок = приоритет (первый найденный wins)."""

    def __init__(self, layers=None):
        self.items = list(layers or [])
        self.renumber()

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __getitem__(self, i):
        return self.items[i]

    def renumber(self):
        for i, l in enumerate(self.items, start=1):
            l.number = i

    def add(self, layer):
        self.items.append(layer)
        self.renumber()
        return layer

    def remove(self, index):
        if 0 <= index < len(self.items):
            self.items.pop(index)
            self.renumber()

    def move(self, index, delta):
        j = index + delta
        if 0 <= index < len(self.items) and 0 <= j < len(self.items):
            self.items[index], self.items[j] = self.items[j], self.items[index]
            self.renumber()

    def toggle(self, index):
        if 0 <= index < len(self.items):
            self.items[index].enabled = not self.items[index].enabled

    def active(self):
        return [l for l in self.items if l.enabled and len(l.polygon) >= 3]

    def layer_at(self, pt, only_active=True):
        for l in (self.active() if only_active else self.items):
            if l.contains(pt):
                return l
        return None

    def hit_index(self, pt):
        """Индекс слоя под курсором (для окна свойств по двойному клику)."""
        for i in range(len(self.items) - 1, -1, -1):
            if self.items[i].contains(pt):
                return i
        return None

    def boundaries_y(self):
        """Отметки границ слоёв — для адаптивной сетки (этап 4)."""
        ys = set()
        for l in self.items:
            for _, y in l.polygon:
                ys.add(y)
        return sorted(ys)

    def to_list(self):
        return [l.to_dict() for l in self.items]

    @classmethod
    def from_list(cls, data):
        out = []
        for d in data or []:
            if "polygon" in d:
                out.append(SoilLayer.from_dict(d))
            else:                                   # старый формат проекта
                out.append(SoilLayer.from_dict({
                    "name": d.get("name", "Грунт"),
                    "polygon": d.get("points", []),
                    "gamma": d.get("gamma", 19.0),
                    "c": d.get("c", d.get("c_dry", 15.0)),
                    "phi": d.get("phi", 18.0),
                    "color": d.get("color", "#FFB3BA"),
                }))
        return cls(out)

    def copy(self):
        return LayerCollection.from_list(self.to_list())


def default_layer_palette(i):
    return COLORS["layer_fill"][i % len(COLORS["layer_fill"])]