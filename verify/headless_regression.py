# SPDX-License-Identifier: MIT
"""verify/headless_regression.py -- PushPull headless regression (freecadcmd).

Run from the repo root:

    freecadcmd verify/headless_regression.py

Exit code 0 and a final "37/37 checks pass" line when green.

Drives the Gui-decoupled ``PushPullController`` by method call -- the same
object the real SoEvent/Qt callbacks drive: "the user dragged 5 mm" is
``update_distance(5)``, "typed 12.5 and pressed Enter" is
``type_char(...)`` + ``key_return()``. What needs a rendered 3D view
(the Coin ghost's scene-graph insertion, real mouse picking) is not
claimed here; the ghost lifecycle is checked with a fake tracker and the
Qt key routing with fake key events (PySide imports fine under
freecadcmd, no QApplication needed for the pure logic).

Checks (one shared document; order matters):
  geom                 1-2    ray/axis closest-point param, parallel fallback
  face_utils           3-9    planar pick accept, edge/curved/bare-solid
                              refusals, loose-face standalone path,
                              face_still_matches, Reversed-face outward
                              normal (probed: normalAt already applies
                              Orientation; the old extra flip inverted the
                              drag axis on every Reversed face)
  controller          10-17   typed buffer rules, cancel, too-small commit,
                              typed commit as Pad, undo as ONE step, pocket,
                              Reversed bottom face pads downward end-to-end
  session leaks       18-20   restart/cancel remove a leftover ghost, commit
                              clears active
  stale selection     21-23   old-feature pick after a tip change is refused
                              (probed: "Face6" silently named a different
                              face), tip/body picks still accepted
  commit rollback     24-28   failed commit aborts its transaction, leaves no
                              half-built feature, restores Body.Tip (probed:
                              removeObject left Tip None) under UndoMode 1
                              and UndoMode 0, Body still usable afterwards
  commit_extrude      29-31   loose face -> solid Part::Extrusion, undoable,
                              reversed direction, too-small refusal
  Qt key routing      32-35   Ctrl/Alt chords neither swallowed nor typed,
                              Esc ends the waiting-for-a-face state, teardown
                              clears the module session slot
  uppercut hook       36      mark_active/mark_inactive fire through a fake
                              toolstate and no-op cleanly without Uppercut
  command wiring      37      static xref: Activated aborts the previous
                              session and consumes the selection, the arming
                              click does not zero-commit, toolstate hooks sit
                              on the start/teardown funnels
"""
import os
import sys
import traceback
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
try:
    import freecad  # FreeCAD's own namespace package (present under freecadcmd)
    freecad.__path__ = [os.path.join(_REPO_ROOT, "freecad")] + list(freecad.__path__)
except ImportError:  # extremely defensive: fall back to plain sys.path
    sys.path.insert(0, _REPO_ROOT)

# freecadcmd imports installed Mod addons at startup; if a PushPullWB copy is
# installed, freecad.PushPullWB is then already in sys.modules and the repo
# path prepend above would be ignored. Drop the cached package so the checks
# always run against THIS checkout.
for _mod in list(sys.modules):
    if _mod == "freecad.PushPullWB" or _mod.startswith("freecad.PushPullWB."):
        del sys.modules[_mod]

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

from freecad.PushPullWB import commit as commit_mod  # noqa: E402
from freecad.PushPullWB import face_utils  # noqa: E402
from freecad.PushPullWB import geom  # noqa: E402
from freecad.PushPullWB.drag_controller import PushPullController  # noqa: E402

EXPECTED_CHECKS = 37
V = App.Vector

_checks = []


def check(name):
    def deco(fn):
        _checks.append((name, fn))
        return fn
    return deco


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def approx(a, b, tol, msg):
    if abs(a - b) > tol:
        raise AssertionError("%s (got %r, want %r +/- %r)" % (msg, a, b, tol))


def face_by_normal(shape, direction, at_z=None):
    """FaceN name of the planar face whose outward normal matches
    ``direction`` (and, optionally, whose center sits at height ``at_z``)."""
    for i, f in enumerate(shape.Faces):
        if not face_utils.is_planar_face(f):
            continue
        n = face_utils.face_normal(f)
        if n.sub(direction).Length < 1e-6:
            if at_z is not None and abs(f.CenterOfMass.z - at_z) > 1e-6:
                continue
            return "Face%d" % (i + 1)
    raise AssertionError("no face with normal %s" % direction)


class FakeGhost(object):
    def __init__(self):
        self.removed = False
        self.offset = None

    def set_offset(self, vector):
        self.offset = vector

    def remove(self):
        self.removed = True


class Fixture(object):
    def __init__(self):
        self.doc = App.newDocument("PushPullRegression")
        # the GUI runs with undo on; transactions are part of what we verify
        self.doc.UndoMode = 1
        self.body = self.doc.addObject("PartDesign::Body", "Body")
        self.box = self.body.newObject("PartDesign::AdditiveBox", "Box")
        self.box.Length = 10
        self.box.Width = 10
        self.box.Height = 10
        self.doc.recompute()
        self.top = face_by_normal(self.box.Shape, V(0, 0, 1))


# -- geom ------------------------------------------------------------------

@check("geom: perpendicular pick ray projects to the drag distance")
def _c(fx):
    # drag axis +Z from origin; ray at height 5 pointing along -X hits it
    s = geom.closest_point_param_on_line_to_ray(
        V(0, 0, 0), V(0, 0, 1), V(10, 0, 5), V(-1, 0, 0))
    approx(s, 5.0, 1e-9, "param along the axis")


@check("geom: ray parallel to the axis falls back to origin projection")
def _c(fx):
    s = geom.closest_point_param_on_line_to_ray(
        V(0, 0, 0), V(0, 0, 1), V(3, 4, 7), V(0, 0, 1))
    approx(s, 7.0, 1e-9, "degenerate fallback projects the ray origin")


# -- face_utils ------------------------------------------------------------

@check("face_utils: planar Body face accepted with outward normal")
def _c(fx):
    pick = face_utils.validate_pick(fx.box, fx.top)
    ok(pick["body"] is fx.body, "body resolved")
    ok(pick["feature"] is fx.box, "feature is the tip")
    ok(not pick["standalone"], "Body path, not standalone")
    approx(pick["normal"].sub(V(0, 0, 1)).Length, 0.0, 1e-9, "outward +Z")


@check("face_utils: edge pick rejected")
def _c(fx):
    try:
        face_utils.validate_pick(fx.box, "Edge1")
    except face_utils.FaceRejected as exc:
        ok("face" in str(exc).lower(), "friendly message")
    else:
        raise AssertionError("edge pick was accepted")


@check("face_utils: curved face rejected")
def _c(fx):
    # a LOOSE curved face (no solid), so the planarity check itself fires
    # rather than the bare-solid scope refusal
    curved_obj = fx.doc.addObject("Part::Feature", "CurvedLoose")
    lateral = None
    for f in Part.makeCylinder(5, 10).Faces:
        if not face_utils.is_planar_face(f):
            lateral = f
    ok(lateral is not None, "cylinder has a curved face")
    curved_obj.Shape = lateral
    fx.doc.recompute()
    try:
        face_utils.validate_pick(curved_obj, "Face1")
    except face_utils.FaceRejected as exc:
        ok("planar" in str(exc), "friendly message")
    else:
        raise AssertionError("curved face was accepted")
    finally:
        fx.doc.removeObject(curved_obj.Name)
        fx.doc.recompute()


@check("face_utils: Reversed face keeps its OUTWARD normal")
def _c(fx):
    # probed on FreeCAD 1.1: a box's bottom face has Orientation
    # 'Reversed' and normalAt already returns the outward (0, 0, -1); the
    # old extra flip pointed the drag axis INTO the solid on every
    # Reversed face, inverting drag direction and the Pad/Pocket sign
    bottom = None
    for f in fx.box.Shape.Faces:
        if abs(f.CenterOfMass.z) < 1e-9 and face_utils.is_planar_face(f):
            bottom = f
    ok(bottom is not None, "bottom face found")
    ok(bottom.Orientation == "Reversed", "precondition: bottom is Reversed")
    n = face_utils.face_normal(bottom)
    approx(n.sub(V(0, 0, -1)).Length, 0.0, 1e-9, "outward -Z, not inward")


@check("face_utils: bare non-Body solid rejected (v1 scope)")
def _c(fx):
    pbox = fx.doc.addObject("Part::Box", "Bare")
    fx.doc.recompute()
    try:
        face_utils.validate_pick(pbox, "Face1")
    except face_utils.FaceRejected as exc:
        ok("Body" in str(exc), "friendly message names the Body requirement")
    else:
        raise AssertionError("bare solid was accepted")
    finally:
        fx.doc.removeObject(pbox.Name)
        fx.doc.recompute()


@check("face_utils: loose planar face accepted as standalone")
def _c(fx):
    loose = fx.doc.addObject("Part::Feature", "Loose")
    loose.Shape = Part.makePlane(10, 10)
    fx.doc.recompute()
    pick = face_utils.validate_pick(loose, "Face1")
    ok(pick["standalone"], "standalone path")
    ok(pick["body"] is None, "no body")
    fx.loose = loose


@check("face_utils: face_still_matches tracks a shape edit")
def _c(fx):
    f = fx.box.Shape.getElement(fx.top)
    area, com = f.Area, f.CenterOfMass
    ok(face_utils.face_still_matches(fx.box, fx.top, area, com),
       "unchanged face matches")
    fx.box.Length = 12
    fx.doc.recompute()
    ok(not face_utils.face_still_matches(fx.box, fx.top, area, com),
       "resized face no longer matches")
    fx.box.Length = 10
    fx.doc.recompute()


# -- controller ------------------------------------------------------------

@check("controller: typed buffer accepts digits/dot, one dot only")
def _c(fx):
    c = PushPullController(fx.doc)
    ok(c.start(fx.box, fx.top)[0], "start")
    for ch in "12.5.":
        c.type_char(ch)
    ok(c.typed_buffer == "12.5", "second dot ignored, got %r" % c.typed_buffer)
    approx(c.distance, 12.5, 1e-9, "typed preview drives the distance")
    c.cancel()


@check("controller: minus toggles the sign, backspace edits")
def _c(fx):
    c = PushPullController(fx.doc)
    ok(c.start(fx.box, fx.top)[0], "start")
    c.type_char("3")
    c.type_char("-")
    ok(c.typed_buffer == "-3", "minus prepends")
    c.type_char("-")
    ok(c.typed_buffer == "3", "second minus flips back")
    c.key_backspace()
    ok(c.typed_buffer == "", "backspace empties")
    c.cancel()


@check("controller: cancel leaves the document untouched")
def _c(fx):
    n = len(fx.doc.Objects)
    c = PushPullController(fx.doc)
    ok(c.start(fx.box, fx.top)[0], "start")
    c.update_distance(5.0)
    c.cancel()
    ok(not c.active, "inactive after cancel")
    ok(len(fx.doc.Objects) == n, "no objects created")


@check("controller: near-zero drag refuses to commit, document untouched")
def _c(fx):
    n = len(fx.doc.Objects)
    c = PushPullController(fx.doc)
    ok(c.start(fx.box, fx.top)[0], "start")
    c.update_distance(1e-6)
    ok(c.commit() is None, "no feature committed")
    ok("too small" in c.last_message, "friendly message")
    ok(len(fx.doc.Objects) == n, "no objects created")
    ok(not c.active, "session ended")


@check("controller: typed 12.5 + Enter commits a Pad, one undo step")
def _c(fx):
    undo_before = fx.doc.UndoCount
    c = PushPullController(fx.doc)
    ok(c.start(fx.box, fx.top)[0], "start")
    for ch in "12.5":
        c.type_char(ch)
    pad = c.key_return()
    ok(pad is not None, "committed: %s" % c.last_message)
    ok(pad.TypeId == "PartDesign::Pad", "Pad for a positive distance")
    approx(float(pad.Length), 12.5, 1e-9, "typed length wins")
    ok(fx.body.Tip is pad, "tip moved to the new feature")
    approx(fx.body.Tip.Shape.Volume, 1000 + 100 * 12.5, 1e-6, "padded volume")
    ok(fx.doc.UndoCount == undo_before + 1,
       "exactly one transaction (got %d -> %d)" % (undo_before, fx.doc.UndoCount))


@check("controller: doc.undo() rolls the whole commit back")
def _c(fx):
    fx.doc.undo()
    fx.doc.recompute()
    ok(fx.doc.getObject("PushPull") is None, "pad gone after one undo")
    ok(fx.body.Tip is fx.box, "tip back on the box")
    approx(fx.body.Tip.Shape.Volume, 1000.0, 1e-6, "box volume restored")


@check("controller: negative drag commits a Pocket")
def _c(fx):
    c = PushPullController(fx.doc)
    ok(c.start(fx.box, fx.top)[0], "start")
    c.update_distance(-3.0)
    pocket = c.commit()
    ok(pocket is not None, "committed: %s" % c.last_message)
    ok(pocket.TypeId == "PartDesign::Pocket", "Pocket for a negative distance")
    approx(float(pocket.Length), 3.0, 1e-9, "positive length")
    approx(fx.body.Tip.Shape.Volume, 1000 - 100 * 3, 1e-6, "pocketed volume")
    fx.doc.undo()
    fx.doc.recompute()
    ok(fx.body.Tip is fx.box, "undo restores the box tip")


@check("controller: pulling the Reversed bottom face pads DOWNWARD")
def _c(fx):
    # end-to-end lock on the face_normal fix: dragging the bottom face
    # 2 mm away from the solid must grow the solid 2 mm in -Z
    bottom = face_by_normal(fx.box.Shape, V(0, 0, -1))
    c = PushPullController(fx.doc)
    ok(c.start(fx.box, bottom)[0], "start on the bottom face")
    # the user pulls 2 mm along the outward normal: a pick ray hitting the
    # axis 2 mm below the face must read as +2, not -2
    s = geom.closest_point_param_on_line_to_ray(
        c.origin, c.normal, V(50, 0, -2), V(-1, 0, 0))
    approx(s, 2.0, 1e-9, "outward drag reads positive")
    c.update_distance(s)
    pad = c.commit()
    ok(pad is not None, "committed: %s" % c.last_message)
    ok(pad.TypeId == "PartDesign::Pad", "outward drag is a Pad")
    approx(pad.Shape.Volume, 1000 + 100 * 2, 1e-6, "grew by 200 mm3")
    approx(pad.Shape.BoundBox.ZMin, -2.0, 1e-6, "grew downward")
    fx.doc.undo()
    fx.doc.recompute()
    ok(fx.body.Tip is fx.box, "undo restores the box tip")


# -- session leaks ---------------------------------------------------------

@check("leak: re-starting a controller removes the leftover ghost")
def _c(fx):
    c = PushPullController(fx.doc)
    ok(c.start(fx.box, fx.top)[0], "first start")
    g = FakeGhost()
    c.ghost = g
    ok(c.start(fx.box, fx.top)[0], "second start")
    ok(g.removed, "old ghost detached from the scene graph")
    c.cancel()


@check("leak: cancel removes the ghost")
def _c(fx):
    c = PushPullController(fx.doc)
    ok(c.start(fx.box, fx.top)[0], "start")
    g = FakeGhost()
    c.ghost = g
    c.cancel()
    ok(g.removed, "ghost detached")
    ok(c.ghost is None, "reference dropped")


@check("leak: commit removes the ghost and deactivates")
def _c(fx):
    c = PushPullController(fx.doc)
    ok(c.start(fx.box, fx.top)[0], "start")
    g = FakeGhost()
    c.ghost = g
    c.update_distance(2.0)
    pad = c.commit()
    ok(pad is not None, "committed")
    ok(g.removed, "ghost detached")
    ok(not c.active, "inactive")
    fx.doc.undo()
    fx.doc.recompute()


# -- stale selection -------------------------------------------------------

@check("stale: old-feature pick after a tip change is refused")
def _c(fx):
    c = PushPullController(fx.doc)
    ok(c.start(fx.box, fx.top)[0], "start")
    c.update_distance(5.0)
    pad = c.commit()
    ok(pad is not None, "pad committed")
    fx.pad = pad
    # The user bug: a Gui.Selection held from before the commit still says
    # (Box, "Face6"), but on the new tip that name is a DIFFERENT face
    # (probed: the 100 mm2 top became a 150 mm2 side). Must refuse, not
    # silently drag the wrong face.
    old_face = fx.box.Shape.getElement(fx.top)
    new_face = pad.Shape.getElement(fx.top)
    ok(abs(old_face.Area - new_face.Area) > 1.0,
       "precondition: the name really shifted meaning")
    try:
        face_utils.validate_pick(fx.box, fx.top)
    except face_utils.FaceRejected as exc:
        ok("stale" in str(exc), "friendly stale message")
    else:
        raise AssertionError("stale pick was accepted")


@check("stale: picking the current tip feature still works")
def _c(fx):
    new_top = face_by_normal(fx.pad.Shape, V(0, 0, 1), at_z=15.0)
    pick = face_utils.validate_pick(fx.pad, new_top)
    ok(pick["feature"] is fx.pad, "tip pick accepted")
    fx.new_top = new_top


@check("stale: picking via the Body object still works")
def _c(fx):
    pick = face_utils.validate_pick(fx.body, fx.new_top)
    ok(pick["feature"] is fx.pad, "body pick resolves to the tip")
    ok(pick["body"] is fx.body, "body kept")


# -- commit rollback -------------------------------------------------------

@check("rollback: failed commit raises CommitError, no leftover object")
def _c(fx):
    names = set(o.Name for o in fx.doc.Objects)
    try:
        commit_mod.commit_pushpull(fx.doc, fx.body, fx.body.Tip, "Face999", 5.0)
    except commit_mod.CommitError:
        pass
    else:
        raise AssertionError("bogus face name committed")
    ok(set(o.Name for o in fx.doc.Objects) == names, "no half-built feature")


@check("rollback: failed commit restores Body.Tip (UndoMode 1)")
def _c(fx):
    tip_before = fx.body.Tip
    try:
        commit_mod.commit_pushpull(fx.doc, fx.body, fx.body.Tip, "Face999", 5.0)
    except commit_mod.CommitError:
        pass
    ok(fx.body.Tip is not None, "tip not left None")
    ok(fx.body.Tip is tip_before, "tip unchanged")


@check("rollback: failed commit leaves no undo step behind")
def _c(fx):
    undo_before = fx.doc.UndoCount
    try:
        commit_mod.commit_pushpull(fx.doc, fx.body, fx.body.Tip, "Face999", 5.0)
    except commit_mod.CommitError:
        pass
    ok(fx.doc.UndoCount == undo_before,
       "transaction aborted, not committed (%d -> %d)"
       % (undo_before, fx.doc.UndoCount))


@check("rollback: UndoMode 0 (headless) failed commit also cleans up")
def _c(fx):
    # freecadcmd documents default to UndoMode 0, where abortTransaction
    # rolls back nothing (probed) -- the explicit cleanup must cover it.
    doc = App.newDocument("PushPullUndo0")
    try:
        ok(doc.UndoMode == 0, "precondition: undo off")
        body = doc.addObject("PartDesign::Body", "B")
        box = body.newObject("PartDesign::AdditiveBox", "X")
        doc.recompute()
        names = set(o.Name for o in doc.Objects)
        try:
            commit_mod.commit_pushpull(doc, body, box, "Face999", 5.0)
        except commit_mod.CommitError:
            pass
        ok(set(o.Name for o in doc.Objects) == names, "no half-built feature")
        ok(body.Tip is not None and body.Tip.Name == box.Name,
           "tip restored, not left None (the probed removeObject bug)")
    finally:
        App.closeDocument(doc.Name)


@check("rollback: a later commit on the same Body still works")
def _c(fx):
    c = PushPullController(fx.doc)
    ok(c.start(fx.pad, fx.new_top)[0], "start on the tip")
    c.update_distance(2.0)
    obj = c.commit()
    ok(obj is not None, "committed after earlier failures: %s" % c.last_message)
    fx.doc.undo()
    fx.doc.recompute()
    ok(fx.body.Tip is fx.pad, "undo restores the pad tip")


# -- commit_extrude (standalone loose face) --------------------------------

@check("extrude: loose face commits to a solid Part::Extrusion, undoable")
def _c(fx):
    undo_before = fx.doc.UndoCount
    c = PushPullController(fx.doc)
    ok(c.start(fx.loose, "Face1")[0], "start on the loose face")
    c.update_distance(4.0)
    ext = c.commit()
    ok(ext is not None, "committed: %s" % c.last_message)
    ok(ext.TypeId == "Part::Extrusion", "parametric extrusion")
    approx(ext.Shape.Volume, 10 * 10 * 4, 1e-6, "solid volume")
    ok(fx.doc.UndoCount == undo_before + 1, "one undo step")
    ext_name = ext.Name  # the object dies with the undo
    fx.doc.undo()
    fx.doc.recompute()
    ok(fx.doc.getObject(ext_name) is None, "undo removes it")


@check("extrude: negative distance goes the other way")
def _c(fx):
    ext = commit_mod.commit_extrude(fx.doc, fx.loose, V(0, 0, 1), -4.0)
    zmin = ext.Shape.BoundBox.ZMin
    approx(zmin, -4.0, 1e-6, "extruded downward")
    fx.doc.removeObject(ext.Name)
    fx.doc.recompute()


@check("extrude: too-small distance refused, document untouched")
def _c(fx):
    n = len(fx.doc.Objects)
    try:
        commit_mod.commit_extrude(fx.doc, fx.loose, V(0, 0, 1), 1e-9)
    except commit_mod.CommitError:
        pass
    else:
        raise AssertionError("near-zero extrude accepted")
    ok(len(fx.doc.Objects) == n, "no objects created")


# -- Qt key routing (commands.py imports headless; no QApplication needed) --

class FakeKeyEvent(object):
    def __init__(self, key, mods):
        self._key = key
        self._mods = mods

    def key(self):
        return self._key

    def modifiers(self):
        return self._mods


class FakeController(object):
    def __init__(self, active=True):
        self.active = active
        self.typed = []
        self.cancelled = False
        self.distance = 0.0
        self.typed_buffer = ""

    def type_char(self, ch):
        self.typed.append(ch)

    def key_backspace(self):
        self.typed.append("<bs>")

    def key_return(self):
        self.typed.append("<ret>")

    def cancel(self):
        self.cancelled = True
        self.active = False


@check("keys: Ctrl/Alt chords are neither swallowed nor typed")
def _c(fx):
    from PySide import QtCore
    from freecad.PushPullWB import commands
    cmd = commands.PushPullCommand()
    cmd.controller = FakeController()
    cmd._sg_callback = object()
    plain = FakeKeyEvent(QtCore.Qt.Key_5, QtCore.Qt.NoModifier)
    keypad = FakeKeyEvent(QtCore.Qt.Key_5, QtCore.Qt.KeypadModifier)
    ctrl = FakeKeyEvent(QtCore.Qt.Key_5, QtCore.Qt.ControlModifier)
    ok(cmd.wants_key(plain), "bare digit claimed")
    ok(cmd.wants_key(keypad), "numpad digit claimed")
    ok(not cmd.wants_key(ctrl), "Ctrl+digit left to the application")
    ok(cmd.handle_key(plain), "bare digit consumed")
    ok(not cmd.handle_key(ctrl), "Ctrl+digit not consumed")
    ok(cmd.controller.typed == ["5"], "only the bare digit reached the buffer")


@check("keys: Esc ends the waiting-for-a-face state too")
def _c(fx):
    from PySide import QtCore
    from freecad.PushPullWB import commands
    cmd = commands.PushPullCommand()
    cmd.controller = FakeController(active=False)  # armed-to-pick, no drag yet
    cmd._sg_callback = object()  # session wired
    commands._current_session = cmd
    esc = FakeKeyEvent(QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier)
    ok(cmd.wants_key(esc), "Esc claimed while the session is wired")
    ok(cmd.handle_key(esc), "Esc consumed")
    ok(cmd._sg_callback is None, "session unhooked")
    ok(commands._current_session is None, "module session slot cleared")


@check("keys: Esc does nothing once the session is gone")
def _c(fx):
    from PySide import QtCore
    from freecad.PushPullWB import commands
    cmd = commands.PushPullCommand()
    esc = FakeKeyEvent(QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier)
    ok(not cmd.wants_key(esc), "no session, Esc not claimed")
    ok(not cmd.handle_key(esc), "no session, Esc not consumed")


@check("keys: abort() cancels the drag and clears the session slot")
def _c(fx):
    from freecad.PushPullWB import commands
    cmd = commands.PushPullCommand()
    cmd.controller = FakeController(active=True)
    cmd._sg_callback = object()
    commands._current_session = cmd
    cmd.abort()
    ok(cmd.controller.cancelled, "controller cancelled")
    ok(cmd._sg_callback is None, "callbacks unhooked")
    ok(commands._current_session is None, "session slot cleared")


# -- Uppercut toolstate hook -----------------------------------------------

@check("uppercut: highlight hook fires through a fake toolstate, no-ops without")
def _c(fx):
    from freecad.PushPullWB import commands
    calls = []
    fake_ts = types.ModuleType("freecad.UppercutWB.toolstate")
    fake_ts.mark_active = lambda name: calls.append(("on", name))
    fake_ts.mark_inactive = lambda name: calls.append(("off", name))
    fake_pkg = types.ModuleType("freecad.UppercutWB")
    fake_pkg.toolstate = fake_ts
    saved = {k: sys.modules.get(k)
             for k in ("freecad.UppercutWB", "freecad.UppercutWB.toolstate")}
    sys.modules["freecad.UppercutWB"] = fake_pkg
    sys.modules["freecad.UppercutWB.toolstate"] = fake_ts
    try:
        commands._tool_started("PushPull_PushPull")
        cmd = commands.PushPullCommand()
        cmd._teardown()  # the single exit funnel must clear the highlight
        ok(("on", "PushPull_PushPull") in calls, "mark_active fired")
        ok(("off", "PushPull_PushPull") in calls, "mark_inactive fired")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    # and with Uppercut absent (import blocked), both must silently no-op
    sys.modules["freecad.UppercutWB"] = None
    try:
        commands._tool_started("PushPull_PushPull")
        commands._tool_finished("PushPull_PushPull")
    finally:
        sys.modules.pop("freecad.UppercutWB", None)


# -- command wiring (static xref for the GUI-only paths) -------------------

@check("wiring: Activated guards re-activation, consumes the selection")
def _c(fx):
    src_path = os.path.join(
        _REPO_ROOT, "freecad", "PushPullWB", "commands.py")
    with open(src_path) as fh:
        src = fh.read()
    activated = src.index("def Activated(")
    teardown = src.index("def _teardown(")
    # session guard: previous session aborted before wiring a new one
    ok(src.index("_current_session.abort()", activated) < src.index(
        "addEventCallback", activated), "abort previous before wiring")
    ok("clearSelection" in src[activated:src.index("def abort(")],
       "auto-start consumes the selection")
    # the arming click while at ~zero distance must not zero-commit
    button = src.index("def _on_mouse_button(")
    ok("MIN_LENGTH" in src[button:src.index("elif state == \"UP\"")],
       "zero-distance click re-arms instead of committing")
    # highlight hooks sit on the start/teardown funnels
    ok("_tool_started(" in src[activated:src.index("def abort(")],
       "mark_active on activation")
    ok("_tool_finished(" in src[teardown:], "mark_inactive in teardown")
    ok("if _current_session is self" in src[teardown:],
       "teardown clears the session slot")


def main():
    fx = Fixture()
    passed = 0
    failures = []
    for idx, (name, fn) in enumerate(_checks, 1):
        try:
            fn(fx)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures.append((idx, name, exc))
            print("[FAIL %2d] %s" % (idx, name))
            traceback.print_exc()
        else:
            passed += 1
            print("[ ok  %2d] %s" % (idx, name))
    total = passed + len(failures)
    print("-" * 64)
    print("%d/%d checks pass" % (passed, total))
    if total != EXPECTED_CHECKS:
        print("WARNING: ran %d checks, expected %d -- update EXPECTED_CHECKS"
              % (total, EXPECTED_CHECKS))
    if failures:
        print("FAILURES:")
        for idx, name, exc in failures:
            print("  %2d. %s: %s" % (idx, name, exc))
        return 1
    return 0


# Not guarded by __name__ == "__main__": stock freecadcmd (for example the
# conda-forge 1.1.0 build) does not set __name__ that way, so a guarded
# harness silently runs zero checks and still exits 0. Run unconditionally;
# os._exit propagates the code without tripping freecadcmd's SystemExit
# handling, and the flush beats freecadcmd's buffered stdout.
rc = main()
sys.stdout.flush()
os._exit(rc)
