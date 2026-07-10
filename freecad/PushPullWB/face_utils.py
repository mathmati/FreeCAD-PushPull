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
            "PushPull only works on a face of a PartDesign Body's tip solid. "
            "This object isn't part of a Body (v1 doesn't offer a Part "
            "Extrude fallback yet) -- pick a face on a PartDesign part instead."
        )
    if body.Tip is None:
        raise FaceRejected("PushPull: this Body has no tip feature yet.")
    return body, body.Tip


def validate_pick(obj, sub_name):
    """Validate a (obj, sub_element_name) pick from Gui.Selection/
    preselection for use as a PushPull drag start.

    Returns a dict describing the validated pick:
        body, feature, face_name, face (Part.Face, resolved via
        feature.getSubObject), origin (Vector, face center of mass),
        normal (Vector, outward unit normal).

    Raises FaceRejected with a user-facing message on any problem
    (non-face sub-element, non-planar face, face not on a Body).
    """
    if not sub_name or not sub_name.startswith("Face"):
        raise FaceRejected("PushPull: select a face, not an edge or vertex.")

    body, feature = resolve_body_and_feature(obj)

    sub = feature.getSubObject(sub_name)
    if sub is None or not isinstance(sub, Part.Face):
        raise FaceRejected("PushPull: could not resolve that face on the Body's tip solid.")

    if not is_planar_face(sub):
        raise FaceRejected("PushPull only supports planar faces (this one is curved).")

    normal = face_normal(sub)
    origin = sub.CenterOfMass

    return {
        "body": body,
        "feature": feature,
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
