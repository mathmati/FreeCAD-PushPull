# SPDX-License-Identifier: MIT
"""FreeCAD Gui.Command for the PushPull workbench.

Interaction model (SketchUp-style):

1. Activate the "Push/Pull" command (toolbar/menu). If a single planar
   face is already selected (normal FreeCAD click-selection, i.e.
   Gui.Selection), the drag starts immediately, armed and ready.
2. Otherwise, click a planar face on a PartDesign Body's tip solid in the
   3D view (uses the live preselection FreeCAD's own selection system
   already computes on hover -- no custom picking code needed).
3. Move the mouse (with or without holding the button) to drag along the
   face's normal -- a cheap Coin ghost + a status-bar readout track the
   live distance; OR type a number (digits, '.', '-') for the precise
   distance.
4. Release after a real drag, or click a second time, or press Enter --
   commits a parametric PartDesign::Pad (outward) or Pocket (inward).
5. Esc at any point cancels cleanly: event callbacks removed, ghost
   cleared, no document changes.

Mouse-move/click detection uses FreeCADGui's dict-style "SoEvent"
callback (the documented, widely-used idiom -- see core Draft's
DraftTools.py / draftguitools/gui_lines.py). Digit/Enter/Escape typing
uses a Qt event filter on the main window (Qt.Key_* constants are stable
and well-documented, unlike the untested string names an SoKeyboardEvent
dict would deliver for anything besides "ESCAPE").
"""
import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from .drag_controller import PushPullController

_ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "Resources",
    "Icons",
)
_ICON_PATH = os.path.join(_ICON_DIR, "pushpull.svg")

_TYPE_KEYS = {
    QtCore.Qt.Key_0: "0", QtCore.Qt.Key_1: "1", QtCore.Qt.Key_2: "2",
    QtCore.Qt.Key_3: "3", QtCore.Qt.Key_4: "4", QtCore.Qt.Key_5: "5",
    QtCore.Qt.Key_6: "6", QtCore.Qt.Key_7: "7", QtCore.Qt.Key_8: "8",
    QtCore.Qt.Key_9: "9", QtCore.Qt.Key_Period: ".", QtCore.Qt.Key_Minus: "-",
}


class PushPullCommand(object):
    """Opens (and drives) a single click-drag-commit PushPull session."""

    def __init__(self):
        self._view = None
        self._sg_callback = None
        self._key_filter = None
        self.controller = None
        self._down_pos = None
        self._moved_since_arm = False

    def GetResources(self):
        return {
            "MenuText": "Push/Pull",
            "ToolTip": (
                "Click a planar face on a PartDesign Body and drag along its "
                "normal to Pad (outward) or Pocket (inward) it -- or click "
                "then type an exact distance and press Enter."
            ),
            "Pixmap": _ICON_PATH,
        }

    def IsActive(self):
        return App.ActiveDocument is not None and Gui.ActiveDocument is not None

    def Activated(self):
        doc = App.ActiveDocument
        self._view = Gui.ActiveDocument.ActiveView
        self.controller = PushPullController(doc, view=self._view)
        self._down_pos = None
        self._moved_since_arm = False

        self._sg_callback = self._view.addEventCallback("SoEvent", self._on_event)
        self._install_key_filter()

        # Convenience entry point: if the user already selected a face the
        # normal FreeCAD way before invoking this command, start right away.
        # Default resolve (=1): SubElementNames come back as plain "FaceN"
        # strings and Object is the actual leaf feature (e.g. the Pad the
        # user clicked), not the wrapping Body -- verified interactively;
        # resolve=0 instead returns Body + an internal element-map-encoded
        # sub-element string, which is not what we want here.
        sel = Gui.Selection.getSelectionEx()
        if len(sel) == 1 and len(sel[0].SubElementNames) == 1:
            ok, msg = self.controller.start(sel[0].Object, sel[0].SubElementNames[0])
            if not ok:
                self._status(msg)
        else:
            self._status("PushPull: click a planar face to start (Esc cancels).")

    # -- Qt keyboard handling ------------------------------------------------
    def _install_key_filter(self):
        # Installed on the QApplication instance, not the main window.
        # FreeCAD binds bare digit keys 0-6 to "set standard view"
        # shortcuts (Front/Top/Right/.../Isometric); Qt's shortcut/QAction
        # dispatch consumes those key events before a filter installed on
        # a specific widget (e.g. the main window) ever sees them --
        # confirmed empirically while building the GUI verification driver
        # (a widget-level filter silently lost every digit keypress to the
        # view-shortcut instead of reaching our handler). An
        # application-level event filter runs earlier in Qt's dispatch and
        # reliably intercepts the digit before it's interpreted as a
        # shortcut, at the cost of suppressing those view shortcuts while
        # a PushPull drag session is open (acceptable: Esc/commit end the
        # session quickly, and this only applies while actively typing a
        # distance).
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        self._key_filter = _KeyFilter(self)
        app.installEventFilter(self._key_filter)

    def _remove_key_filter(self):
        if self._key_filter is not None:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.removeEventFilter(self._key_filter)
            self._key_filter = None

    def wants_key(self, event):
        """True if this command would act on ``event``'s key right now.

        Used to decide whether to accept() a QEvent.ShortcutOverride --
        without this, FreeCAD's built-in "set standard view" shortcuts
        bound to bare digit keys 0-6 win the race and consume the
        keypress before our QEvent.KeyPress handler ever sees it
        (confirmed empirically while building the GUI verification
        driver: a bare KeyPress handler alone silently lost every digit
        typed during a drag session to the view-switch shortcut instead).
        """
        if self.controller is None or not self.controller.active:
            return False
        key = event.key()
        return key in _TYPE_KEYS or key in (
            QtCore.Qt.Key_Escape,
            QtCore.Qt.Key_Return,
            QtCore.Qt.Key_Enter,
            QtCore.Qt.Key_Backspace,
        )

    def handle_key(self, event):
        """Called by _KeyFilter for every QEvent.KeyPress while this
        command is active. Returns True if the event was consumed."""
        if self.controller is None or not self.controller.active:
            return False
        key = event.key()
        if key == QtCore.Qt.Key_Escape:
            self.controller.cancel()
            self._teardown()
            return True
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.controller.key_return()
            self._teardown()
            return True
        if key == QtCore.Qt.Key_Backspace:
            self.controller.key_backspace()
            return True
        if key in _TYPE_KEYS:
            self.controller.type_char(_TYPE_KEYS[key])
            return True
        return False

    # -- SoEvent (mouse) handling --------------------------------------------
    def _on_event(self, arg):
        etype = arg.get("Type")
        if etype == "SoKeyboardEvent":
            if arg.get("Key") == "ESCAPE":
                self.controller.cancel()
                self._teardown()
            return
        if etype == "SoLocation2Event":
            self._on_mouse_move(arg)
            return
        if etype == "SoMouseButtonEvent":
            self._on_mouse_button(arg)
            return

    def _on_mouse_move(self, arg):
        pos = arg.get("Position")
        if pos is not None and self._down_pos is not None:
            dx = pos[0] - self._down_pos[0]
            dy = pos[1] - self._down_pos[1]
            if (dx * dx + dy * dy) ** 0.5 > PushPullController.DRAG_PIXEL_THRESHOLD:
                self._moved_since_arm = True

        if self.controller is None or not self.controller.active or pos is None:
            return
        ray_origin, ray_dir = self._pick_ray(pos)
        if ray_origin is not None:
            self.controller.update_from_ray(ray_origin, ray_dir)

    def _on_mouse_button(self, arg):
        if arg.get("Button") != "BUTTON1":
            return
        state = arg.get("State")
        if state == "DOWN":
            if self.controller.active:
                # second click while armed -> commit at current distance
                self.controller.commit()
                self._teardown()
                return
            obj, sub = self._preselection_pick()
            if obj is None:
                self._status("PushPull: click a planar face to start (Esc cancels).")
                return
            ok, msg = self.controller.start(obj, sub)
            self._status(msg)
            if ok:
                self._down_pos = arg.get("Position")
                self._moved_since_arm = False
        elif state == "UP":
            if self.controller.active and self._moved_since_arm:
                self.controller.commit()
                self._teardown()

    # -- helpers --------------------------------------------------------
    def _preselection_pick(self):
        try:
            presel = Gui.Selection.getPreselection()
        except Exception:
            presel = None
        if presel is None or not presel.SubElementNames:
            return None, None
        sub = presel.SubElementNames[0]
        obj = App.ActiveDocument.getObject(presel.Object.Name)
        return obj, sub

    def _pick_ray(self, pos):
        """Unproject a 2D screen position to a 3D pick ray (origin,
        direction), the same technique core Draft uses (WorkingPlane.
        getApparentPoint): ``view.getPoint(x, y)`` plus the camera position
        (perspective) or view direction (orthographic)."""
        try:
            view = self._view
            pt = view.getPoint(pos[0], pos[1])
            if view.getCameraType() == "Perspective":
                camera = view.getCameraNode()
                p = camera.getField("position").getValue()
                cam_pos = App.Vector(p[0], p[1], p[2])
                ray_dir = pt.sub(cam_pos)
                return cam_pos, ray_dir
            else:
                ray_dir = view.getViewDirection()
                return pt, ray_dir
        except Exception:
            return None, None

    def _status(self, msg):
        try:
            Gui.getMainWindow().statusBar().showMessage(msg, 5000)
        except Exception:
            pass

    def _teardown(self):
        if self._view is not None and self._sg_callback is not None:
            try:
                self._view.removeEventCallback("SoEvent", self._sg_callback)
            except Exception:
                pass
            self._sg_callback = None
        self._remove_key_filter()
        self._down_pos = None
        self._moved_since_arm = False


class _KeyFilter(QtCore.QObject):
    """Thin, application-level Qt event filter forwarding keyboard input to
    the active PushPullCommand while a drag session is open.

    Handles two event types:
      - QEvent.ShortcutOverride: accept() it (when it's a key we care
        about) to tell Qt "don't treat this as a shortcut" -- otherwise
        FreeCAD's built-in digit-key standard-view shortcuts win the race
        and the real KeyPress for e.g. '5' never arrives at all.
      - QEvent.KeyPress: the actual typed-distance/commit/cancel logic.
    """

    def __init__(self, command):
        super().__init__()
        self._command = command

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QtCore.QEvent.ShortcutOverride:
            if self._command.wants_key(event):
                event.accept()
            return False
        if etype == QtCore.QEvent.KeyPress:
            if self._command.handle_key(event):
                return True
        return False


def register():
    Gui.addCommand("PushPull_PushPull", PushPullCommand())
