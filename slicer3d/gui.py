"""Desktop window: load models, place them, preview layers, write the print file.

Start with:  python -m slicer3d gui   (or the Slicer3D.bat launcher)
"""

from __future__ import annotations

import os
import sys
import traceback

import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QListWidget, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSlider, QSpinBox, QSplitter, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__, primitives, settings_store
from .export import estimate_print_time, export
from .printers import PRINTERS
from .scene import Scene, SceneObject
from .slicer import SliceError, Slicer
from .supports import SupportSettings

try:  # The 3D view needs OpenGL; the rest of the app works without it.
    import pyvista as pv
    from pyvistaqt import QtInteractor
except Exception:  # pragma: no cover - depends on the machine
    pv = None
    QtInteractor = None

MODEL_COLOR = "#8fb8e8"
SELECTED_COLOR = "#ffb347"
SUPPORT_COLOR = "#c9c9c9"


def to_polydata(mesh):
    faces = np.hstack([np.full((len(mesh.faces), 1), 3), mesh.faces]).astype(np.int64).ravel()
    return pv.PolyData(np.asarray(mesh.vertices, dtype=np.float64), faces)


def spin(lo, hi, step, decimals=2, suffix=""):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


class ExportWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, mesh, printer, path):
        super().__init__()
        self.mesh, self.printer, self.path = mesh, printer, path

    def run(self):
        try:
            result = export(self.mesh, self.printer, self.path, progress=lambda d, t: self.progress.emit(d, t))
            self.finished.emit(result)
        except SliceError as exc:
            self.failed.emit(str(exc))
        except Exception:  # show anything unexpected instead of crashing
            self.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Slicer3D {__version__}")
        self.resize(1400, 860)
        key = settings_store.last_printer()
        self.scene = Scene(settings_store.load_printer(key))
        self.slicer: Slicer | None = None
        self._updating = False
        self._thread = None

        self._redraw_timer = QTimer(self, singleShot=True, interval=120)
        self._redraw_timer.timeout.connect(self.redraw)

        splitter = QSplitter()
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_view(), "3D view")
        self.tabs.addTab(self._build_layers(), "Layers")
        self.tabs.currentChanged.connect(self._tab_changed)
        splitter.addWidget(self.tabs)
        panel = QScrollArea()
        panel.setWidgetResizable(True)
        panel.setWidget(self._build_panel())
        panel.setMinimumWidth(360)
        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 1)
        self.setCentralWidget(splitter)

        open_action = QAction("Add model...", self, shortcut="Ctrl+O", triggered=self.add_model)
        self.menuBar().addMenu("&File").addAction(open_action)

        self._load_printer_fields()
        self.redraw()

    # -- layout -------------------------------------------------------------
    def _build_view(self):
        if QtInteractor is None:
            label = QLabel("3D view unavailable (OpenGL or PyVista missing).\nYou can still slice and use the Layers tab.")
            label.setAlignment(Qt.AlignCenter)
            self.plotter = None
            return label
        self.plotter = QtInteractor(self)
        self.plotter.set_background("#20242c", top="#3a4252")
        return self.plotter.interactor

    def _build_layers(self):
        w = QWidget()
        layout = QHBoxLayout(w)
        left = QVBoxLayout()
        self.layer_image = QLabel("Press 'Preview layers' to slice the plate.")
        self.layer_image.setAlignment(Qt.AlignCenter)
        self.layer_image.setMinimumSize(400, 300)
        self.layer_image.setStyleSheet("background:#000;color:#bbb")
        self.layer_info = QLabel("")
        left.addWidget(self.layer_image, 1)
        left.addWidget(self.layer_info)
        self.layer_slider = QSlider(Qt.Vertical)
        self.layer_slider.setEnabled(False)
        self.layer_slider.valueChanged.connect(self.show_layer)
        layout.addLayout(left, 1)
        layout.addWidget(self.layer_slider)
        return w

    def _build_panel(self):
        panel = QWidget()
        v = QVBoxLayout(panel)

        g = QGroupBox("Printer")
        f = QFormLayout(g)
        self.printer_combo = QComboBox()
        for key, p in PRINTERS.items():
            self.printer_combo.addItem(p.name, key)
        self.printer_combo.setCurrentIndex(list(PRINTERS).index(self.scene.printer.key))
        self.printer_combo.currentIndexChanged.connect(self.change_printer)
        f.addRow(self.printer_combo)
        v.addWidget(g)

        g = QGroupBox("Models")
        gv = QVBoxLayout(g)
        row = QHBoxLayout()
        add = QPushButton("Add model...")
        add.clicked.connect(self.add_model)
        shape = QToolButton(text="Add shape")
        shape.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(shape)
        for name in ("cube", "cylinder", "cone", "orientation"):
            menu.addAction(name.capitalize(), lambda n=name: self.add_shape(n))
        shape.setMenu(menu)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_selected)
        arrange = QPushButton("Arrange")
        arrange.clicked.connect(self.arrange)
        for b in (add, shape, remove, arrange):
            row.addWidget(b)
        gv.addLayout(row)
        self.object_list = QListWidget()
        self.object_list.setMaximumHeight(120)
        self.object_list.currentRowChanged.connect(self._select)
        gv.addWidget(self.object_list)
        v.addWidget(g)

        g = QGroupBox("Selected model")
        f = QFormLayout(g)
        self.f_x = spin(-500, 500, 1, suffix=" mm")
        self.f_y = spin(-500, 500, 1, suffix=" mm")
        self.f_rx = spin(-360, 360, 15, 1, " deg")
        self.f_ry = spin(-360, 360, 15, 1, " deg")
        self.f_rz = spin(-360, 360, 15, 1, " deg")
        self.f_scale = spin(1, 2000, 10, 1, " %")
        self.f_supports = QCheckBox("Raft and supports")
        for label, widget in (("X", self.f_x), ("Y", self.f_y), ("Rotate X", self.f_rx), ("Rotate Y", self.f_ry),
                              ("Rotate Z", self.f_rz), ("Scale", self.f_scale)):
            f.addRow(label, widget)
            widget.valueChanged.connect(self._object_edited)
        f.addRow(self.f_supports)
        self.f_supports.toggled.connect(self._object_edited)
        row = QHBoxLayout()
        flat = QPushButton("Lay flat")
        flat.clicked.connect(self.lay_flat)
        reset = QPushButton("Reset rotation")
        reset.clicked.connect(self.reset_rotation)
        row.addWidget(flat)
        row.addWidget(reset)
        f.addRow(row)
        self.size_label = QLabel("")
        f.addRow("Size", self.size_label)
        self.object_box = g
        v.addWidget(g)

        g = QGroupBox("Supports")
        f = QFormLayout(g)
        self.f_lift = spin(1, 30, 0.5, 1, " mm")
        self.f_lift.setValue(self.scene.support_settings.lift_mm)
        self.f_lift.valueChanged.connect(self._supports_edited)
        self.f_spacing = spin(1, 10, 0.5, 1, " mm")
        self.f_spacing.setValue(self.scene.support_settings.spacing_mm)
        self.f_spacing.valueChanged.connect(self._supports_edited)
        f.addRow("Lift above raft", self.f_lift)
        f.addRow("Support spacing", self.f_spacing)
        v.addWidget(g)

        g = QGroupBox("Print settings")
        f = QFormLayout(g)
        self.p_layer = spin(0.01, 0.3, 0.01, 3, " mm")
        self.p_exp = spin(0.1, 60, 0.1, 2, " s")
        self.p_bexp = spin(1, 200, 1, 1, " s")
        self.p_bcount = QSpinBox()
        self.p_bcount.setRange(1, 50)
        self.p_trans = QSpinBox()
        self.p_trans.setRange(0, 50)
        self.p_wait = spin(0, 30, 0.5, 1, " s")
        for label, widget in (("Layer height", self.p_layer), ("Exposure", self.p_exp),
                              ("Bottom exposure", self.p_bexp), ("Bottom layers", self.p_bcount),
                              ("Transition layers", self.p_trans), ("Wait before cure", self.p_wait)):
            f.addRow(label, widget)
            widget.valueChanged.connect(self._print_settings_edited)
        save = QPushButton("Save as my default for this printer")
        save.clicked.connect(self.save_defaults)
        f.addRow(save)
        v.addWidget(g)

        self.problems = QLabel("")
        self.problems.setStyleSheet("color:#d9534f")
        self.problems.setWordWrap(True)
        v.addWidget(self.problems)

        preview = QPushButton("Preview layers")
        preview.clicked.connect(self.prepare_layers)
        v.addWidget(preview)
        self.export_button = QPushButton("Slice and save print file...")
        self.export_button.setMinimumHeight(36)
        self.export_button.clicked.connect(self.export_file)
        v.addWidget(self.export_button)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        v.addStretch(1)
        self._refresh_object_fields()
        return panel

    # -- helpers --------------------------------------------------------------
    @property
    def selected(self) -> SceneObject | None:
        row = self.object_list.currentRow()
        return self.scene.objects[row] if 0 <= row < len(self.scene.objects) else None

    def _changed(self, redraw=True):
        self.slicer = None
        self.layer_slider.setEnabled(False)
        problems = self.scene.problems()
        self.problems.setText("\n".join(problems))
        self.export_button.setEnabled(bool(self.scene.objects) and not problems)
        if redraw:
            self._redraw_timer.start()

    def _refresh_list(self, select=None):
        self.object_list.blockSignals(True)
        self.object_list.clear()
        for o in self.scene.objects:
            self.object_list.addItem(o.name)
        self.object_list.blockSignals(False)
        if select is not None and select in self.scene.objects:
            self.object_list.setCurrentRow(self.scene.objects.index(select))
        self._refresh_object_fields()

    def _refresh_object_fields(self):
        o = self.selected
        self.object_box.setEnabled(o is not None)
        if o is None:
            self.size_label.setText("")
            return
        self._updating = True
        self.f_x.setValue(o.x)
        self.f_y.setValue(o.y)
        self.f_rx.setValue(o.rot_x)
        self.f_ry.setValue(o.rot_y)
        self.f_rz.setValue(o.rot_z)
        self.f_scale.setValue(o.scale * 100)
        self.f_supports.setChecked(o.supports)
        self._updating = False
        sx, sy, sz = o.size
        self.size_label.setText(f"{sx:.2f} x {sy:.2f} x {sz:.2f} mm")

    def _load_printer_fields(self):
        e = self.scene.printer.exposure
        self._updating = True
        self.p_layer.setValue(e.layer_height_mm)
        self.p_exp.setValue(e.exposure_s)
        self.p_bexp.setValue(e.bottom_exposure_s)
        self.p_bcount.setValue(e.bottom_layers)
        self.p_trans.setValue(e.transition_layers)
        self.p_wait.setValue(e.wait_before_cure_s)
        self._updating = False

    # -- actions ----------------------------------------------------------------
    def add_model(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add model", "", "3D models (*.stl *.obj *.3mf *.ply);;All files (*)")
        last = None
        for path in paths:
            try:
                last = self.scene.add_file(path)
            except Exception as exc:
                QMessageBox.warning(self, "Could not load model", f"{os.path.basename(path)}:\n{exc}")
        if last:
            self._refresh_list(last)
            self._changed()
            self.reset_camera()

    def add_shape(self, name):
        obj = self.scene.add(SceneObject(name, primitives.SHAPES[name]()))
        self._refresh_list(obj)
        self._changed()
        self.reset_camera()

    def remove_selected(self):
        if self.selected:
            self.scene.remove(self.selected)
            self._refresh_list()
            self._changed()

    def arrange(self):
        self.scene.arrange()
        self._refresh_object_fields()
        self._changed()

    def _select(self, _row):
        self._refresh_object_fields()
        self._redraw_timer.start()

    def _object_edited(self, *_):
        o = self.selected
        if self._updating or o is None:
            return
        o.x, o.y = self.f_x.value(), self.f_y.value()
        o.rot_x, o.rot_y, o.rot_z = self.f_rx.value(), self.f_ry.value(), self.f_rz.value()
        o.scale = self.f_scale.value() / 100
        o.supports = self.f_supports.isChecked()
        sx, sy, sz = o.size
        self.size_label.setText(f"{sx:.2f} x {sy:.2f} x {sz:.2f} mm")
        self._changed()

    def lay_flat(self):
        if self.selected:
            if not self.selected.lay_flat():
                self.summary.setText("This model has no flat face to lay it on.")
            self._refresh_object_fields()
            self._changed()

    def reset_rotation(self):
        if self.selected:
            o = self.selected
            o.rot_x = o.rot_y = o.rot_z = 0.0
            self._refresh_object_fields()
            self._changed()

    def _supports_edited(self, *_):
        s = self.scene.support_settings
        from dataclasses import replace
        self.scene.support_settings = replace(s, lift_mm=self.f_lift.value(), spacing_mm=self.f_spacing.value())
        self._changed()

    def _print_settings_edited(self, *_):
        if self._updating:
            return
        self.scene.printer = self.scene.printer.with_exposure(
            layer_height_mm=self.p_layer.value(), exposure_s=self.p_exp.value(),
            bottom_exposure_s=self.p_bexp.value(), bottom_layers=self.p_bcount.value(),
            transition_layers=self.p_trans.value(), wait_before_cure_s=self.p_wait.value(),
        )
        self._changed(redraw=False)

    def save_defaults(self):
        settings_store.save_printer(self.scene.printer)
        self.summary.setText(f"Saved these settings as your default for the {self.scene.printer.name}.")

    def change_printer(self, index):
        key = self.printer_combo.itemData(index)
        self.scene.printer = settings_store.load_printer(key)
        settings_store.remember_printer(key)
        self._load_printer_fields()
        self.scene.arrange()
        self._refresh_object_fields()
        self._changed()
        self.reset_camera()

    # -- 3D view ----------------------------------------------------------------
    def redraw(self):
        if self.plotter is None:
            return
        pl = self.plotter
        pl.clear()
        p = self.scene.printer
        w, h, z = p.display_width_mm, p.display_height_mm, p.machine_z_mm
        pl.add_mesh(pv.Box((-w / 2, w / 2, -h / 2, h / 2, -0.5, 0)), color="#555b66")
        pl.add_mesh(pv.Box((-w / 2, w / 2, -h / 2, h / 2, 0, z)), style="wireframe", color="#7a8494", opacity=0.4)
        # Small marker at the front-left corner so orientation is obvious.
        pl.add_mesh(pv.Sphere(radius=2, center=(-w / 2 + 4, -h / 2 + 4, 0.5)), color="#d9534f")
        sel = self.selected
        for o in self.scene.objects:
            try:
                model = o.oriented()
                full = o.placed(self.scene.support_settings)
            except Exception as exc:
                self.problems.setText(f"{o.name}: {exc}")
                continue
            color = SELECTED_COLOR if o is sel else MODEL_COLOR
            if o.supports and "supports" in full.metadata:
                model = full.metadata["part"]
                pl.add_mesh(to_polydata(full.metadata["supports"]), color=SUPPORT_COLOR)
            pl.add_mesh(to_polydata(model), color=color, smooth_shading=True)
        pl.render()

    def reset_camera(self):
        if self.plotter is None:
            return
        self.redraw()
        self.plotter.view_isometric()
        self.plotter.reset_camera()

    # -- layers -----------------------------------------------------------------
    def _tab_changed(self, index):
        if index == 1 and self.slicer is None and self.scene.objects and not self.scene.problems():
            self.prepare_layers()

    def prepare_layers(self):
        try:
            self.slicer = Slicer(self.scene.build_mesh(), self.scene.printer)
        except (SliceError, ValueError) as exc:
            self.layer_image.setText(str(exc))
            return
        n = self.slicer.layer_count
        self.layer_slider.setRange(0, n - 1)
        self.layer_slider.setEnabled(True)
        self.tabs.setCurrentIndex(1)
        seconds = estimate_print_time(self.scene.printer, n)
        self.summary.setText(f"{n} layers, {self.slicer.height_mm:.2f} mm tall, about "
                             f"{seconds // 3600}h {seconds % 3600 // 60:02d}m to print.")
        self.layer_slider.setValue(min(self.layer_slider.value(), n - 1))
        self.show_layer(self.layer_slider.value())

    def show_layer(self, index):
        if self.slicer is None:
            return
        layer = self.slicer.slice_layer(index)
        img = np.ascontiguousarray(layer.pixels)
        rows, cols = img.shape
        p = self.scene.printer
        # Correct for non-square pixels so shapes look right.
        width_mm, height_mm = cols * p.pixel_width_mm, rows * p.pixel_height_mm
        qimg = QImage(img.data, cols, rows, cols, QImage.Format_Grayscale8).copy()
        target = self.layer_image.size()
        scale = min(target.width() / width_mm, target.height() / height_mm)
        pix = QPixmap.fromImage(qimg).scaled(max(1, int(width_mm * scale)), max(1, int(height_mm * scale)),
                                             Qt.IgnoreAspectRatio, Qt.FastTransformation)
        self.layer_image.setPixmap(pix)
        area = layer.lit_pixels * p.pixel_width_mm * p.pixel_height_mm
        self.layer_info.setText(f"Layer {index + 1} of {self.slicer.layer_count}   z = {layer.z_mm:.3f} mm   "
                                f"exposure {p.exposure.exposure_for_layer(index):.2f} s   area {area:.1f} mm²")

    # -- export -----------------------------------------------------------------
    def export_file(self):
        p = self.scene.printer
        default = (self.scene.objects[0].name if len(self.scene.objects) == 1 else "plate") + "." + p.extension
        path, _ = QFileDialog.getSaveFileName(self, "Save print file", default, f"{p.name} (*.{p.extension})")
        if not path:
            return
        if not path.lower().endswith("." + p.extension):
            path += "." + p.extension
        try:
            mesh = self.scene.build_mesh()
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot slice", str(exc))
            return
        self.export_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self._thread = QThread(self)
        self._worker = ExportWorker(mesh, p, path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(lambda d, t: (self.progress.setMaximum(t), self.progress.setValue(d)))
        self._worker.finished.connect(self._export_done)
        self._worker.failed.connect(self._export_failed)
        self._thread.start()

    def _finish_thread(self):
        self._thread.quit()
        self._thread.wait()
        self.progress.setVisible(False)
        self.export_button.setEnabled(True)

    def _export_done(self, result):
        self._finish_thread()
        self.summary.setText(result.summary() + f"\nSaved to {result.path}")

    def _export_failed(self, message):
        self._finish_thread()
        QMessageBox.warning(self, "Slicing failed", message)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
