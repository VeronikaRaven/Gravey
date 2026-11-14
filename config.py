# config.py
# Все константы и настройки для программы

# Нормативы (СП 22.13330.2016, СП 116.13330.2012)
SAFETY_FACTORS = {
    "temporary": 1.15,      # Временный
    "permanent": 1.30,      # Постоянный
    "responsible": 1.50     # Ответственный
}

# Цвета интерфейса и графики
COLORS = {
    "bg": "#f0f0f0",
    "canvas_bg": "#ffffff",
    "grid": "#e0e0e0",
    "axis": "#000000",
    "slope": "#333333",
    "water": "#1e90ff",
    "slip_circle": "#ff0000",
    "layer_fill": ["#FFB3BA", "#BAFFC9", "#BAE1FF", "#FFFFBA", "#FFDFBA"],
    "layer_line": "#666666"
}

# Настройки холста (по умолчанию)
CANVAS_WIDTH = 900
CANVAS_HEIGHT = 600
DEFAULT_SCALE = 20  # 1 метр = 20 пикселей по умолчанию (можно менять зумом)
GRID_STEP_M = 1.0   # Шаг сетки в метрах

# Стандартные грунты (если пользователь не задал свои)
DEFAULT_SOIL_TYPES = {
    "Песок": {"gamma": 18.0, "c_dry": 1.0, "c_wet": 0.5, "phi": 30.0},
    "Суглинок": {"gamma": 19.0, "c_dry": 15.0, "c_wet": 8.0, "phi": 18.0},
    "Глина": {"gamma": 20.0, "c_dry": 25.0, "c_wet": 15.0, "phi": 12.0}
}