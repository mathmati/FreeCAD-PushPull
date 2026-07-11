# SPDX-License-Identifier: MIT
"""Face picking/validation helpers shared by the headless controller and the
interactive Gui command.

Every function here works against plain FreeCAD document objects/shapes --
no Coin/pivy, no Gui.Selection reads (the caller passes in the object and
sub-element name it already resolved from Gui.Selection/preselection). This
keeps face_utils importable and testable under plain ``freecadcmd``.
"""
import FreeCAD as App
import Part


class FaceRejected(Exception):
    """Raised (and caught by the caller) with a friendly, user-facing message
    when a pick cannot be used for a PushPull drag."""


def is_planar_face(face):
    """True if a Part.Face's underlying surface is a plane."""
    try:
        return isinstance(face.Surface, Part.Plane)
    except Exception:
        return False


def face_normal(face):
    """Outward unit normal of a planar face, correcting for the face's own
    Orientation flag (a well-known FreeCAD gotcha: a Face's geometric
    ``normalAt`` can point *into* the solid if Orientation == 'Reversed')."""
    normal = face.normalAt(0, 0)
    if face.Orientation == "Reversed":
        normal = normal.multiply(-1)
    normal.normalize()
    return normal


def resolve_body_and_feature(obj):
    """Given the document object the user actually clicked on in the 3D
    view, return ``(body, feature)`` where ``feature`` is the object whose
    ``Shape`` the face index should be read against for a Pad/Pocket
    ``Profile`` (PartDesign's own tip feature, since a Body's displayed
    Shape mirrors its Tip), and ``body`` is the owning ``PartDesign::Body``.

    Raises FaceRejected with a friendly message if ``obj`` is not part of a
    PartDesign Body (v1 scope, per v1 scope: bare Part solids
    get a friendly message, not a fallback Part::Extrude path).
    """
    if obj is None:
        raise FaceRejected("PushPull: nothing selected.")

    if obj.TypeId == "PartDesign::Body":
        body = obj
        feature = obj.Tip
        if feature is None:
            raise FaceRejected("PushPull: this Body has no tip feature yet.")
        return body, feature

    # Object may be a PartDesign feature itself (Pad/Pocket/Box/etc. inside
    # a Body) -- walk InList to find the owning Body.
    body = None
    for parent in getattr(obj, "InList", []):
        if parent.TypeId == "PartDesign::Body":
            body = parent
            break
    if body is None:
        raise FaceRejected(
            "PushPull works on a face of a PartDesign Body's tip solid, or a "
            "standalone drawn face (which it extrudes into a solid). This is a "
            "face of a bare non-Body solid, which needs a boolean to push in "
            "place and isn't supported yet -- use a PartDesign Body, or draw a "
            "loose face (e.g. with SketchLayer/Draft)."
        )
    if body.Tip is None:
        raise FaceRejected("PushPull: this Body has no tip feature yet.")
    return body, body.Tip


def validate_pick(obj, sub_name):
    """Validate a (obj, sub_element_name) pick from Gui.Selection/
    preselection for use as a PushPull drag start.

    Two accepted cases:
      * a planar face on a **PartDesign Body**'s tip solid -> committed as a
        parametric Pad/Pocket (``standalone`` False, ``body`` set);
      * a **standalone planar face** on any other object (a bare Part::Feature
        face, e.g. one drawn by the SketchLayer addon or Draft) -> committed
        as a parametric ``Part::Extrusion`` into a solid (``standalone`` True,
        ``body`` None). This is the SketchUp "draw a face, then push it up"
        path (added v0.2.0).

    Raises FaceRejected with a user-facing message on any problem (non-face
    sub-element, non-planar face, nothing selected).
    """
    if obj is None:
        raise FaceRejected("PushPull: nothing selected.")
    if not sub_name or not sub_name.startswith("Face"):
        raise FaceRejected("PushPull: select a face, not an edge or vertex.")

    try:
        body, feature = resolve_body_and_feature(obj)
        standalone = False
    except FaceRejected:
        # The standalone-extrude path applies to a LOOSE planar face -- an
        # object whose shape carries no solid (a Part::Feature face/shell as
        # drawn by SketchLayer or Draft). A face of a bare *solid* would need
        # a boolean to push/pull in place and remains out of scope.
        shape = getattr(obj, "Shape", None)
        if shape is not None and len(shape.Solids) > 0:
            raise
        body, feature, standalone = None, obj, True

    sub = feature.getSubObject(sub_name)
    if sub is None or not isinstance(sub, Part.Face):
        raise FaceRejected("PushPull: could not resolve that face.")

    if not is_planar_face(sub):
        raise FaceRejected("PushPull only supports planar faces (this one is curved).")

    normal = face_normal(sub)
    origin = sub.CenterOfMass

    return {
        "body": body,
        "feature": feature,
        "standalone": standalone,
        "face_name": sub_name,
        "face": sub,
        "origin": origin,
        "normal": normal,
    }


def face_still_matches(feature, face_name, expected_area, expected_com, tol=1e-4):
    """Defensive re-check at commit time: does ``face_name`` on ``feature``'s
    *current* shape still look like the face we originally picked?

    This does not "solve" FreeCAD's topological naming problem (see the
    design notes and README) -- it's a cheap sanity check that catches the
    obvious case where a recompute between pick and commit silently shifted
    which geometry ``Face7`` refers to, so PushPull can fail loudly instead
    of silently padding/pocketing the wrong face.
    """
    try:
        sub = feature.getSubObject(face_name)
    except Exception:
        return False
    if sub is None or not isinstance(sub, Part.Face):
        return False
    if abs(sub.Area - expected_area) > max(tol, expected_area * 1e-6):
        return False
    if sub.CenterOfMass.sub(expected_com).Length > 1e-3:
        return False
    return True
