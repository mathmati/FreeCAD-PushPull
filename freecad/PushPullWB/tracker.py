# SPDX-License-Identifier: MIT
"""FaceGhostTracker: the cheap Coin3D-only drag preview.

Mirrors the pattern used by core Draft's ``draftguitools/gui_trackers.py``
(``Tracker``/``ghostTracker``): build the ghost's geometry ONCE (here, a
tessellated copy of the picked face + its outer-wire outline), insert it
into the 3D view's scene graph, then on every mouse-move tick only update
an ``SoTransform``'s translation. No OCCT/Part call and no document
recompute happens per tick -- this is precisely the design choice the
prior-art review flagged as the difference
between a usable tool and the trap that reportedly stalled Design456's
push/pull attempt (a real boolean recompute on every mouse-move event).

Like Draft's Tracker, scene-graph insertion/removal is deferred with
``QTimer.singleShot(0, ...)`` because it must not happen while Coin is
mid-traversal (e.g. from inside the SoEvent callback that triggered it).
Transform-only updates (``set_offset``) are safe to do directly, same as
``ghostTracker.move()``.
"""
import pivy.coin as coin
import FreeCADGui as Gui
from PySide import QtCore

GHOST_LINE_COLOR = (0.95, 0.55, 0.10)
GHOST_FILL_COLOR = (0.95, 0.65, 0.25)
GHOST_FILL_TRANSPARENCY = 0.55


def _defer(func, *args):
    QtCore.QTimer.singleShot(0, lambda: func(*args))


def _wire_coords(face):
    """Rough polyline outline of a planar face's outer wire (discretized --
    cheap, computed once at drag start, never per-tick)."""
    try:
        pts = face.OuterWire.discretize(Number=32)
    except Exception:
        pts = [v.Point for v in face.OuterWire.Vertexes]
    return [tuple(p) for p in pts]


def _fill_triangles(face):
    """Coarse tessellation of the face for a translucent fill (once, at
    drag start)."""
    try:
        verts, facets = face.tessellate(1.0)
        return [tuple(v) for v in verts], facets
    except Exception:
        return [], []


class FaceGhostTracker:
    """A translucent, translatable copy of the picked face used as the
    live drag preview."""

    def __init__(self, face):
        self.view = Gui.ActiveDocument.ActiveView
        self.trans = coin.SoTransform()
        self.trans.translation.setValue((0, 0, 0))

        content = coin.SoSeparator()
        content.addChild(self.trans)

        # translucent fill
        verts, facets = _fill_triangles(face)
        if verts and facets:
            fill_sep = coin.SoSeparator()
            material = coin.SoMaterial()
            material.diffuseColor = GHOST_FILL_COLOR
            material.transparency = GHOST_FILL_TRANSPARENCY
            fill_sep.addChild(material)
            coords = coin.SoCoordinate3()
            coords.point.setValues(0, len(verts), verts)
            fill_sep.addChild(coords)
            faceset = coin.SoIndexedFaceSet()
            idx = []
            for tri in facets:
                idx.extend([tri[0], tri[1], tri[2], -1])
            faceset.coordIndex.setValues(0, len(idx), idx)
            fill_sep.addChild(faceset)
            content.addChild(fill_sep)

        # outline
        wire_pts = _wire_coords(face)
        if wire_pts:
            line_sep = coin.SoSeparator()
            drawstyle = coin.SoDrawStyle()
            drawstyle.lineWidth = 2.5
            line_sep.addChild(drawstyle)
            color = coin.SoBaseColor()
            color.rgb = GHOST_LINE_COLOR
            line_sep.addChild(color)
            coords = coin.SoCoordinate3()
            coords.point.setValues(0, len(wire_pts), wire_pts)
            line_sep.addChild(coords)
            lineset = coin.SoLineSet()
            lineset.numVertices.setValue(len(wire_pts))
            line_sep.addChild(lineset)
            content.addChild(line_sep)

        self.switch = coin.SoSwitch()
        self.switch.setName("PushPullGhost")
        self.switch.addChild(content)
        self.switch.whichChild = -1

        _defer(self._insert)

    def _scene_graph(self):
        try:
            return self.view.getSceneGraph()
        except Exception:
            return None

    def _insert(self):
        sg = self._scene_graph()
        if sg is not None and self.switch is not None:
            sg.addChild(self.switch)

    def _detach(self):
        sg = self._scene_graph()
        if sg is not None and self.switch is not None and sg.findChild(self.switch) >= 0:
            sg.removeChild(self.switch)

    def show(self):
        if self.switch is not None:
            self.switch.whichChild = 0

    def set_offset(self, vector):
        """Cheap per-tick update: move the ghost's transform. ``vector`` is
        an ``App.Vector`` (already normal * signed distance)."""
        if self.trans is not None:
            self.trans.translation.setValue((vector.x, vector.y, vector.z))

    def remove(self):
        switch = self.switch
        self.switch = None
        self.trans = None
        if switch is not None:
            _defer(self._detach_switch, switch)

    def _detach_switch(self, switch):
        sg = self._scene_graph()
        if sg is not None and sg.findChild(switch) >= 0:
            sg.removeChild(switch)
