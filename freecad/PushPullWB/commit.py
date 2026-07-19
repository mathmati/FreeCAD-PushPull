# SPDX-License-Identifier: MIT
"""Commits a validated PushPull drag as a real parametric PartDesign feature.

Confirmed (see verify/headless_regression.py and the build-session probe
scripts) against a real FreeCAD 1.1: PartDesign::Pad and PartDesign::Pocket
both accept a face directly as ``Profile`` -- ``(feature, [faceName])`` --
no Sketch object required. ``Body.newObject(...)`` automatically appends
into the Body's Group, wires ``BaseFeature`` to the previous tip, and moves
``Body.Tip`` to the new feature once the document is recomputed.
"""
import Part

#: Below this length (mm), a drag is treated as "didn't really move" and
#: cancelled rather than committed as a zero/near-zero-length feature.
MIN_LENGTH = 1e-3


class CommitError(Exception):
    """Friendly, user-facing message for why a commit couldn't happen."""


def _rollback(doc, new_name, body=None, prev_tip=None):
    """Undo a failed commit attempt. abortTransaction alone is enough in
    the GUI (UndoMode 1), but with UndoMode 0 (headless freecadcmd, probed)
    it rolls back nothing, so the half-built feature is also removed
    explicitly and -- crucially -- Body.Tip is restored: doc.removeObject
    of a Body's tip feature leaves Tip None (probed), which would make
    every later PushPull on that Body fail with "no tip feature yet"."""
    doc.abortTransaction()
    try:
        if new_name and doc.getObject(new_name) is not None:
            doc.removeObject(new_name)
    except Exception:
        pass
    try:
        if body is not None and body.Tip is None and prev_tip is not None \
                and doc.getObject(prev_tip.Name) is not None:
            body.Tip = prev_tip
    except Exception:
        pass
    try:
        doc.recompute()
    except Exception:
        pass


def _rollback_names(doc, names):
    """Undo a failed multi-object commit attempt (the seeded-Body path):
    abort the transaction, then -- for UndoMode 0, where aborting rolls
    back nothing (probed) -- explicitly remove every object created so far,
    newest first, so no half-built Body/binder wreckage survives."""
    doc.abortTransaction()
    for name in names:
        try:
            if name and doc.getObject(name) is not None:
                doc.removeObject(name)
        except Exception:
            pass
    try:
        doc.recompute()
    except Exception:
        pass


def _hide(obj):
    """Hide a helper object in the 3D view/tree (ViewObject is None under
    freecadcmd -- guard it)."""
    try:
        if obj.ViewObject is not None:
            obj.ViewObject.Visibility = False
    except Exception:
        pass


def commit_pushpull(doc, body, feature, face_name, distance, name_hint="PushPull",
                    through=False):
    """Create a PartDesign::Pad (distance > 0, dragged away from the solid
    along the face's outward normal) or PartDesign::Pocket (distance < 0,
    dragged into the solid), using ``face_name`` on ``feature`` directly as
    the Profile. Returns the new feature object. Recomputes the document.

    ``through=True`` (only meaningful for a push, distance < 0) commits the
    Pocket with ``Type='ThroughAll'`` instead of a numeric Length: the
    caller detected that the drag stopped exactly at a back face, and a
    Length that lands epsilon-short of the far side leaves a ~1e-7 membrane
    (probed), so the robust open-a-hole form is used instead.

    The whole build runs inside one document transaction, so a committed
    PushPull is a single Undo step in the GUI and a failed one is rolled
    back (plus the explicit cleanup in :func:`_rollback` for UndoMode 0).

    Raises CommitError for a too-small distance (nothing meaningful to
    commit) or if the resulting feature fails to compute validly (e.g. the
    dragged distance would self-intersect the existing solid) -- in which
    case the document is left exactly as it was before the attempt.
    """
    if abs(distance) < MIN_LENGTH:
        raise CommitError("PushPull: drag distance too small, nothing to commit.")

    if distance > 0:
        feature_type = "PartDesign::Pad"
        length = distance
    else:
        feature_type = "PartDesign::Pocket"
        length = -distance

    prev_tip = body.Tip
    new_name = None
    doc.openTransaction("PushPull")
    try:
        new_obj = body.newObject(feature_type, name_hint)
        new_name = new_obj.Name
        new_obj.Profile = (feature, [face_name])
        if through and distance < 0:
            new_obj.Type = "ThroughAll"
        else:
            new_obj.Length = length
        doc.recompute()
    except Exception as exc:
        _rollback(doc, new_name, body, prev_tip)
        raise CommitError(f"PushPull: recompute failed ({exc}); commit aborted.")

    state = list(getattr(new_obj, "State", []))
    shape_ok = True
    try:
        shape_ok = new_obj.Shape.isValid() and not new_obj.Shape.isNull()
    except Exception:
        shape_ok = False

    if "Invalid" in state or not shape_ok:
        _rollback(doc, new_name, body, prev_tip)
        raise CommitError(
            "PushPull: that distance produces an invalid solid (likely "
            "self-intersection); try a smaller distance."
        )

    doc.commitTransaction()
    return new_obj


def commit_seed_body(doc, feature, face_name, normal, distance, name_hint="PushPull"):
    """Commit a standalone planar face (a bare ``Part::Feature`` face, e.g.
    drawn by the SketchLayer addon or Draft -- one that belongs to no
    PartDesign Body) by seeding a new ``PartDesign::Body``: a hidden
    ``SubShapeBinder`` capturing exactly the picked sub-face, plus a Pad
    with the binder as its Profile. Returns the new Pad. Recomputes the
    document.

    Why not a ``Part::Extrusion`` of the whole object (the old path): an
    object holding several loose faces (e.g. a ring plus the disc that
    "split" it, the FreeCAD analog of a circle drawn on a SketchUp
    rectangle) extruded EVERY face, silently filling the hole (probed: two
    overlapping solids, volume 9600 instead of 8695.2). The binder captures
    only ``face_name``, so holes survive exactly -- and the result is a
    normal Body whose every later push/pull flows through the proven
    Pad/Pocket path instead of dead-ending on a bare solid.

    Invisible constraints (probed on FreeCAD 1.1):
      * the Pad extrudes along the face's ORIENTED normal (a
        Reversed-orientation profile face pads the other way), so the sign
        of ``distance`` alone decides ``Reversed``;
      * a binder happily copies a geometrically INVALID face (bad
        inner-wire orientation, routine in scripted/imported geometry) and
        the Pad then computes "Up-to-date" but with the hole filled and an
        invalid shape -- so an invalid source face is rebuilt first via
        ``Part::FaceMakerBullseye`` into a hidden static fallback feature
        (non-parametric, but correct).
    """
    if abs(distance) < MIN_LENGTH:
        raise CommitError("PushPull: drag distance too small, nothing to commit.")

    created = []  # newest first, the removal order for _rollback_names
    doc.openTransaction("PushPull")
    try:
        sub = feature.getSubObject(face_name)
        if sub is None or not isinstance(sub, Part.Face):
            raise CommitError("PushPull: could not resolve that face.")
        source, source_face = feature, face_name
        if not sub.isValid():
            fixed = Part.makeFace(list(sub.Wires), "Part::FaceMakerBullseye")
            fallback = doc.addObject("Part::Feature", name_hint + "Face")
            created.insert(0, fallback.Name)
            fallback.Shape = fixed
            _hide(fallback)
            source, source_face = fallback, "Face1"
        body = doc.addObject("PartDesign::Body", name_hint + "Body")
        created.insert(0, body.Name)
        # a new Body auto-creates an Origin group (origin, axes, planes,
        # point -- 8 objects, probed); abortTransaction alone leaves them
        # orphaned under UndoMode 0, so record them for explicit removal
        # right after the Body itself
        try:
            origin = body.Origin
            if origin is not None:
                extras = [origin.Name]
                extras.extend(
                    of.Name for of in getattr(origin, "OriginFeatures", []))
                idx = created.index(body.Name) + 1
                created[idx:idx] = extras
        except Exception:
            pass
        binder = body.newObject("PartDesign::SubShapeBinder", name_hint + "Binder")
        created.insert(0, binder.Name)
        binder.Support = [(source, (source_face,))]
        _hide(binder)
        pad = body.newObject("PartDesign::Pad", name_hint)
        created.insert(0, pad.Name)
        pad.Profile = binder
        pad.Length = abs(distance)
        pad.Reversed = distance < 0
        doc.recompute()
    except CommitError:
        _rollback_names(doc, created)
        raise
    except Exception as exc:
        _rollback_names(doc, created)
        raise CommitError(f"PushPull: recompute failed ({exc}); commit aborted.")

    shape_ok = True
    try:
        shape_ok = pad.Shape.isValid() and not pad.Shape.isNull() and len(pad.Shape.Solids) >= 1
    except Exception:
        shape_ok = False
    if "Invalid" in list(getattr(pad, "State", [])) or not shape_ok:
        _rollback_names(doc, created)
        raise CommitError(
            "PushPull: could not extrude that face into a valid solid; "
            "check the face is planar and the distance is non-zero."
        )
    doc.commitTransaction()
    return pad
