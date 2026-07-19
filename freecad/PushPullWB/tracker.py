# SPDX-License-Identifier: MIT
"""FaceGhostTracker: the cheap Coin3D-only drag preview.

Mirrors the pattern used by core Draft's ``draftguitools/gui_trackers.py``
(``Tracker``/``ghostTracker``): build the ghost's geometry ONCE (here, a
tessellated copy of the picked face, its wire outlines, and the prism side
edges), insert it into the 3D view's scene graph, then on every mouse-move
tick only update an ``SoTransform``'s translation plus the side edges' far
endpoints. No OCCT/Part call and no document recompute happens per tick --
this is precisely the design choice the prior-art review flagged as the
difference between a usable tool and the trap that reportedly stalled
Design456's push/pull attempt (a real boolean recompute on every
mouse-move event).

The ghost previews the whole extruded volume, SketchUp-style: the static
base outline (every wire of the face, so holes show), the same outlines
riding an ``SoTransform`` at the live drag distance (the far cap), and one
straight side edge per wire vertex connecting the two. Only the side
edges' far endpoints are rewritten per tick (a handful of ``set1Value``
coordinate writes); the wire coordinates themselves are computed once at
drag start and never rebuilt.

Like Draft's Tracker, scene-graph insertion/removal is deferred with
``QTimer.singleShot(0, ...)`` because it must not happen while Coin is
mid-traversal (e.g. from inside the SoEvent callback that triggered it).
Transform/coordinate-only updates (``set_offset``) are safe to do
directly, same as ``ghostTracker.move()``.

The pure coordinate helpers (``ghost_outlines``, ``ghost_side_bases``)
have no Coin/Gui dependency, so this module imports -- and they are
tested -- under plain ``freecadcmd``; only instantiating FaceGhostTracker
needs the GUI stack.
"""
try:
    import pivy.coin as coin
    import FreeCADGui as Gui
    from PySide import QtCore
except Exception:  # pragma: no cover - headless import (freecadcmd)
    coin = None
    Gui = None
    QtCore = None

GHOST_LINE_COLOR = (0.95, 0.55, 0.10)
GHOST_FILL_COLOR = (0.95, 0.65, 0.25)
GHOST_FILL_TRANSPARENCY = 0.55
GHOST_SIDE_LINE_WIDTH = 1.5
GHOST_OUTLINE_WIDTH = 2.5


def _defer(func, *args):
    QtCore.QTimer.singleShot(0, lambda: func(*args))


def ghost_outlines(face):
    """Closed polyline (list of xyz tuples) for EVERY wire of the face --
    outer boundary and hole outlines alike, one polyline per wire, so a
    holed face's ghost shows its holes. Discretized once at drag start,
    never per-tick."""
    outlines = []
    for wire in face.Wires:
        try:
            pts = [tuple(p) for p in wire.discretize(Number=32)]
        except Exception:
            pts = [tuple(v.Point) for v in wire.Vertexes]
            if pts:
                pts.append(pts[0])
        if len(pts) >= 2:
            outlines.append(pts)
    return outlines


def ghost_side_bases(face, curve_samples=8):
    """Base points (xyz tuples) of the prism ghost's side edges: one per
    wire vertex, for every wire. A wire without at least two real vertices
    (a full circle has one seam vertex) gets ``curve_samples`` evenly
    spaced points instead. The matching far endpoint of each side edge is
    the base point plus the live drag offset."""
    pts = []
    for wire in face.Wires:
        vpts = [tuple(v.Point) for v in wire.Vertexes]
        if len(vpts) < 2:
            try:
                # drop the duplicated closing point of a closed wire
                vpts = [tuple(p) for p in
                        wire.discretize(Number=curve_samples + 1)][:-1]
            except Exception:
                pass
        pts.extend(vpts)
    return pts


class FaceGhostTracker:
    """A translucent, translatable prism preview of the picked face: base
    outline, far cap (fill + outline) riding one SoTransform, and side
    edges whose far endpoints follow the same offset."""

    def __init__(self, face):
        self.view = Gui.ActiveDocument.ActiveView
        self.trans = coin.SoTransform()
        self.trans.translation.setValue((0, 0, 0))

        outlines = ghost_outlines(face)
        self._side_bases = ghost_side_bases(face)

        content = coin.SoSeparator()

        # static base outline (all wires; not under the transform)
        base_lines = self._outline_node(outlines, GHOST_OUTLINE_WIDTH)
        if base_lines is not None:
            content.addChild(base_lines)

        # side edges: base point -> base point + offset; only the far
        # endpoints (odd coordinate indices) are rewritten per tick
        self._side_coords = None
        if self._side_bases:
            side_sep = coin.SoSeparator()
            drawstyle = coin.SoDrawStyle()
            drawstyle.lineWidth = GHOST_SIDE_LINE_WIDTH
            side_sep.addChild(drawstyle)
            color = coin.SoBaseColor()
            color.rgb = GHOST_LINE_COLOR
            side_sep.addChild(color)
            self._side_coords = coin.SoCoordinate3()
            doubled = []
            for p in self._side_bases:
                doubled.append(p)
                doubled.append(p)
            self._side_coords.point.setValues(0, len(doubled), doubled)
            side_sep.addChild(self._side_coords)
            lineset = coin.SoLineSet()
            counts = [2] * len(self._side_bases)
            lineset.numVertices.setValues(0, len(counts), counts)
            side_sep.addChild(lineset)
            content.addChild(side_sep)

        # far cap: translucent fill + the same outlines, under the transform
        moved = coin.SoSeparator()
        moved.addChild(self.trans)
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
            moved.addChild(fill_sep)
        far_lines = self._outline_node(outlines, GHOST_OUTLINE_WIDTH)
        if far_lines is not None:
            moved.addChild(far_lines)
        content.addChild(moved)

        self.switch = coin.SoSwitch()
        self.switch.setName("PushPullGhost")
        self.switch.addChild(content)
        self.switch.whichChild = -1

        _defer(self._insert)

    @staticmethod
    def _outline_node(outlines, line_width):
        """One SoSeparator drawing every polyline in ``outlines`` (one
        SoLineSet segment per wire)."""
        if not outlines:
            return None
        sep = coin.SoSeparator()
        drawstyle = coin.SoDrawStyle()
        drawstyle.lineWidth = line_width
        sep.addChild(drawstyle)
        color = coin.SoBaseColor()
        color.rgb = GHOST_LINE_COLOR
        sep.addChild(color)
        coords = coin.SoCoordinate3()
        flat = [p for outline in outlines for p in outline]
        coords.point.setValues(0, len(flat), flat)
        sep.addChild(coords)
        lineset = coin.SoLineSet()
        counts = [len(outline) for outline in outlines]
        lineset.numVertices.setValues(0, len(counts), counts)
        sep.addChild(lineset)
        return sep

    def _scene_graph(self):
        try:
            return self.view.getSceneGraph()
        except Exception:
            return None

    def _insert(self):
        sg = self._scene_graph()
        if sg is not None and self.switch is not None:
            sg.addChild(self.switch)

    def show(self):
        if self.switch is not None:
            self.switch.whichChild = 0

    def set_offset(self, vector):
        """Cheap per-tick update: move the far cap's transform and the side
        edges' far endpoints. ``vector`` is an ``App.Vector`` (already
        normal * signed distance). No geometry is rebuilt."""
        if self.trans is not None:
            self.trans.translation.setValue((vector.x, vector.y, vector.z))
        if self._side_coords is not None:
            for i, (x, y, z) in enumerate(self._side_bases):
                self._side_coords.point.set1Value(
                    2 * i + 1, (x + vector.x, y + vector.y, z + vector.z))

    def remove(self):
        switch = self.switch
        self.switch = None
        self.trans = None
        self._side_coords = None
        if switch is not None:
            _defer(self._detach_switch, switch)

    def _detach_switch(self, switch):
        sg = self._scene_graph()
        if sg is not None and sg.findChild(switch) >= 0:
            sg.removeChild(switch)


def _fill_triangles(face):
    """Coarse tessellation of the face for a translucent fill (once, at
    drag start)."""
    try:
        verts, facets = face.tessellate(1.0)
        return [tuple(v) for v in verts], facets
    except Exception:
        return [], []
