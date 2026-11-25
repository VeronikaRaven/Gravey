import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch

from calculator import SlopeCalculator
from config import SOIL_PRESETS, get_soil_by_params
from main import App


POINTS = [(0, 0), (20, 0), (20, 5), (10, 10), (0, 10)]


class SoilTests(unittest.TestCase):
    def test_default_soil_supports_calculation(self):
        calculator = SlopeCalculator(POINTS, [])
        self.assertEqual(calculator.get_soil_props_at_y(0), (19.0, 15.0, 18.0))
        circles = calculator.calculate_bishop()
        self.assertTrue(circles)
        self.assertTrue(all(math.isfinite(circle[0]) for circle in circles))

    def test_preset_labels_match_their_properties(self):
        for name, soil in SOIL_PRESETS.items():
            with self.subTest(soil=name):
                self.assertEqual(get_soil_by_params(soil['c'], soil['phi']), name)

    def test_base_soil_is_used_outside_additional_layer(self):
        calculator = SlopeCalculator(
            POINTS, [],
            layers=[{'y_bottom': 2, 'y_top': 4, 'gamma': 20, 'c': 25, 'phi': 12}],
            soil_props={'gamma': 18, 'c': 1, 'phi': 30},
        )
        self.assertEqual(calculator.get_soil_props_at_y(3), (20, 25, 12))
        self.assertEqual(calculator.get_soil_props_at_y(6), (18, 1, 30))


class AppTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.app = App(self.root)
        self.root.update()

    def test_startup_creates_all_tabs(self):
        self.assertEqual(len(self.app.notebook.tabs()), 3)

    def test_layers_are_visible_before_stability_calculation(self):
        self.app.canvas.points = POINTS.copy()
        with patch('main.simpledialog.askfloat', side_effect=[2.0, 4.0]), \
                patch('main.simpledialog.askinteger', return_value=1):
            self.app.add_layer()
        polygons = [item for item in self.app.canvas.find_all()
                    if self.app.canvas.type(item) == 'polygon']
        self.assertEqual(len(polygons), 2)

        self.app.notebook.select(1)
        self.app.copy_slope_geo()
        self.app.canvas.points = []
        self.app.filt_canvas.draw()
        polygons = [item for item in self.app.filt_canvas.find_all()
                    if self.app.filt_canvas.type(item) == 'polygon']
        self.assertEqual(len(polygons), 2)

    def test_zoom_out_does_not_break_grid(self):
        self.app.canvas.scale = 2.0
        self.app.canvas.on_zoom(SimpleNamespace(num=0, delta=-120))
        self.assertGreater(self.app.canvas.scale, 0)
        self.app.canvas.draw()

    def test_all_calculation_tabs_render_results(self):
        self.app.canvas.points = POINTS.copy()
        self.app.canvas.water_points = [(0, 3), (20, 3)]
        self.app.calculate_stability()
        fs = float(self.app.result_fs.cget('text').split('=')[1])
        self.assertTrue(math.isfinite(fs))
        for index, canvas, calculate in (
            (1, self.app.filt_canvas, self.app.calculate_filtration),
            (2, self.app.therm_canvas, self.app.calculate_thermal),
        ):
            with self.subTest(tab=index):
                self.app.notebook.select(index)
                self.app.copy_slope_geo()
                before = len(canvas.find_all())
                calculate()
                self.assertGreater(len(canvas.find_all()), before)

    def test_project_and_report_can_be_saved(self):
        self.app.canvas.points = POINTS.copy()
        self.app.canvas.water_points = [(0, 3), (20, 3)]
        self.app.canvas.inclusions = [[(2, 2), (4, 2), (3, 4)]]
        self.app.calculate_stability()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            project = Path(directory) / 'project.json'
            report = Path(directory) / 'report.txt'
            with patch('main.filedialog.asksaveasfilename', return_value=str(project)), \
                    patch('main.messagebox.showinfo'):
                self.app.save_project()
            self.assertEqual(len(json.loads(project.read_text(encoding='utf-8'))['points']), 5)
            self.app.clear_canvas()
            with patch('main.filedialog.askopenfilename', return_value=str(project)), \
                    patch('main.messagebox.showinfo'), \
                    patch('main.messagebox.showerror') as error:
                self.app.load_project()
            error.assert_not_called()
            self.assertEqual(self.app.canvas.points, POINTS)
            self.assertEqual(self.app.canvas.inclusions, [[(2, 2), (4, 2), (3, 4)]])
            self.app.calculate_stability()
            with patch('main.filedialog.asksaveasfilename', return_value=str(report)), \
                    patch('main.messagebox.showinfo'):
                self.app.save_report()
            self.assertIn(self.app.result_fs.cget('text'), report.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
