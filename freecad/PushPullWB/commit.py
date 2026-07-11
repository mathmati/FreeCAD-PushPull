# SPDX-License-Identifier: MIT
"""Commits a validated PushPull drag as a real parametric PartDesign feature.

Confirmed (see verify/headless_regression.py and the build-session probe
scripts) against a real FreeCAD 1.1: PartDesign::Pad and PartDesign::Pocket
both accept a face directly as ``Profile`` -- ``(feature, [faceName])`` --
no Sketch object required. ``Body.newObject(...)`` automatically appends
into the Body's Group, wires ``BaseFeature`` to the previous tip, and moves
``Body.Tip`` to the new feature once the document is recomputed.
"""
import FreeCAD as App

#: Below this length (mm), a drag is treated as "didn't really move" and
#: cancelled rather than committed as a zero/near-zero-length feature.
MIN_LENGTH = 1e-3


class CommitError(Exception):
    """Friendly, user-facing message for why a commit couldn't happen."""


def commit_pushpull(doc, body, feature, face_name, distance, name_hint="PushPull"):
    """Create a PartDesign::Pad (distance > 0, dragged away from the solid
    along the face's outward normal) or PartDesign::Pocket (distance < 0,
    dragged into the solid), using ``face_name`` on ``feature`` directly as
    the Profile. Returns the new feature object. Recomputes the document.

    Raises CommitError for a too-small distance (nothing meaningful to
    commit) or if the resulting feature fails to compute validly (e.g. the
    dragged distance would self-intersect the existing solid) -- in which
    case the half-built feature is removed again so the document is left
    exactly as it was before the attempt.
    """
    if abs(distance) < MIN_LENGTH:
        raise CommitError("PushPull: drag distance too small, nothing to commit.")

    if distance > 0:
        feature_type = "PartDesign::Pad"
        length = distance
    else:
        feature_type = "PartDesign::Pocket"
        length = -distance

    new_obj = body.newObject(feature_type, name_hint)
    new_obj.Profile = (feature, [face_name])
    new_obj.Length = length
    try:
        doc.recompute()
    except Exception as exc:
        doc.removeObject(new_obj.Name)
        doc.recompute()
        raise CommitError(f"PushPull: recompute failed ({exc}); commit aborted.")

    state = list(getattr(new_obj, "State", []))
    shape_ok = True
    try:
        shape_ok = new_obj.Shape.isValid() and not new_obj.Shape.isNull()
    except Exception:
        shape_ok = False

    if "Invalid" in state or not shape_ok:
        doc.removeObject(new_obj.Name)
        doc.recompute()
        raise CommitError(
            "PushPull: that distance produces an invalid solid (likely "
            "self-intersection); try a smaller distance."
        )

    return new_obj


def commit_extrude(doc, feature, normal, distance, name_hint="PushPull"):
    """Commit a standalone planar face (a bare ``Part::Feature`` face, e.g.
    drawn by the SketchLayer addon or Draft -- one that belongs to no
    PartDesign Body) as a parametric ``Part::Extrusion`` into a solid, along
    the face's outward normal by ``distance`` (negative = extrude the other
    way). Returns the new ``Part::Extrusion`` object. Recomputes the document.

    Kept parametric on purpose (``Base`` + ``LengthFwd`` stay editable after
    the fact) so a SketchUp-drawn face pushed into a box remains adjustable,
    exactly like a Pad. On an invalid result the half-built feature is
    removed so the document is left unchanged.
    """
    if abs(distance) < MIN_LENGTH:
        raise CommitError("PushPull: drag distance too small, nothing to commit.")

    ext = doc.addObject("Part::Extrusion", name_hint)
    ext.Base = feature
    ext.DirMode = "Custom"
    ext.Dir = App.Vector(normal)
    ext.LengthFwd = abs(distance)
    ext.Reversed = distance < 0
    ext.Solid = True
    ext.Symmetric = False
    try:
        doc.recompute()
    except Exception as exc:
        doc.removeObject(ext.Name)
        doc.recompute()
        raise CommitError(f"PushPull: recompute failed ({exc}); commit aborted.")

    shape_ok = True
    try:
        shape_ok = ext.Shape.isValid() and not ext.Shape.isNull() and len(ext.Shape.Solids) >= 1
    except Exception:
        shape_ok = False
    if "Invalid" in list(getattr(ext, "State", [])) or not shape_ok:
        doc.removeObject(ext.Name)
        doc.recompute()
        raise CommitError(
            "PushPull: could not extrude that face into a valid solid; "
            "check the face is planar and the distance is non-zero."
        )
    return ext
