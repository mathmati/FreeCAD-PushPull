# SPDX-License-Identifier: MIT
"""Face picking/validation helpers shared by the headless controller and the
interactive Gui command.

Every function here works against plain FreeCAD document objects/shapes --
no Coin/pivy, no Gui.Selection reads (the caller passes in the object and
sub-element name it already resolved from Gui.Selection/preselection). This
keeps face_utils importable and testable under plain ``freecadcmd``.
"""
import random

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
    """Outward unit normal of a planar face.

    ``Part.Face.normalAt`` already applies the face's Orientation flag
    (probed on FreeCAD 1.1: a box's bottom face has Orientation
    'Reversed' and normalAt(0, 0) returns the correct outward (0, 0, -1)).
    The old extra multiply(-1) on 'Reversed' therefore double-flipped:
    every Reversed face got an INWARD drag axis, inverting the drag
    direction and the Pad/Pocket sign on those faces. The
    orientation-unaware gotcha applies to ``Surface.normal``, not to
    ``Face.normalAt``."""
    normal = face.normalAt(0, 0)
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
        by seeding a new PartDesign Body from exactly the picked face (see
        commit.commit_seed_body; ``standalone`` True, ``body`` None). This is
        the SketchUp "draw a face, then push it up" path.

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

    if not standalone and obj is not feature and obj is not body:
        # ``obj`` is an OLDER feature of the Body (a stale Gui.Selection
        # held from before a commit moved the tip). ``sub_name`` was
        # recorded against the old shape, and face indices do not carry
        # over a tip change (probed headless: after one Pad, "Face6" of
        # the old Box names a different side face on the new tip). Only
        # accept the pick if the same-named face on the clicked object is
        # geometrically the same face on the current tip.
        try:
            old = obj.getSubObject(sub_name)
        except Exception:
            old = None
        same = (
            old is not None
            and isinstance(old, Part.Face)
            and abs(old.Area - sub.Area) <= max(1e-4, sub.Area * 1e-6)
            and old.CenterOfMass.sub(sub.CenterOfMass).Length <= 1e-3
        )
        if not same:
            raise FaceRejected(
                "PushPull: that selection is stale (the model changed since "
                "the face was selected) -- click the face again."
            )

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


#: a candidate region smaller than this (mm2) is boolean noise, not a
#: clickable region
REGION_MIN_AREA = 1e-6

#: max distance (mm) from the 3D pick point to a region face for the click
#: to count as "inside" it -- the pick point comes from FreeCAD's own
#: geometry pick, so real hits measure ~1e-7 (probed); anything past this
#: is an unconfident resolve and falls back to the whole face
REGION_POINT_TOL = 1e-4


def coplanar_splitters(doc, body, feature, face, normal, tol=1e-5):
    """Candidate "splitter" objects for SketchUp-style region picking:
    drawn shapes lying flat on the picked Body ``face``. Returns a list of
    ``(obj, [face, ...])`` pairs, where each listed face is a planar face
    of ``obj`` (or a face built from a loose closed wire of it) coplanar
    with ``face`` -- same plane within ``tol`` mm, parallel normal either
    way (the drawn winding is irrelevant, probed: common() keeps the
    picked face's orientation regardless).

    Only genuine drawn shapes qualify: solids never split, PartDesign/
    Sketcher/App objects live inside Bodies and never split, an object a
    SubShapeBinder points at is a hidden helper a previous commit already
    consumed, and an object hidden in the GUI is invisible to the user so
    it must not invisibly split (ViewObject is None under freecadcmd --
    treated as visible).
    """
    out = []
    plane_point = face.CenterOfMass  # on the plane even when inside a hole
    for obj in doc.Objects:
        if obj is body or obj is feature:
            continue
        if obj.TypeId.startswith(("PartDesign::", "Sketcher::", "App::")):
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull() or len(shape.Solids) > 0:
            continue
        if any(p.TypeId == "PartDesign::SubShapeBinder"
               for p in getattr(obj, "InList", [])):
            continue
        vo = getattr(obj, "ViewObject", None)
        if vo is not None and not getattr(vo, "Visibility", True):
            continue
        faces = list(shape.Faces)
        if not faces:
            # a drawn closed loop that never became a face (SketchLayer
            # commits closed paths as bare wires too) splits just the same
            for w in shape.Wires:
                if not w.isClosed():
                    continue
                try:
                    faces.append(Part.Face(w))
                except Exception:
                    pass
        coplanar = []
        for sf in faces:
            if not is_planar_face(sf):
                continue
            try:
                sn = face_normal(sf)
            except Exception:
                continue
            if abs(sn.dot(normal)) < 0.9999:
                continue
            if abs(sf.CenterOfMass.sub(plane_point).dot(normal)) > tol:
                continue
            coplanar.append(sf)
        if coplanar:
            out.append((obj, coplanar))
    return out


def resolve_region(face, splitters, point, tol=REGION_POINT_TOL):
    """Resolve a 3D pick ``point`` on ``face`` to the sub-region bounded by
    the ``splitters`` (as returned by :func:`coplanar_splitters`) -- the
    SketchUp behavior where a closed loop drawn on a face splits it into
    separately pullable pieces. Returns ``(region_face, used_objects)``:
    the region actually clicked, plus the splitter objects that bounded it
    (to be hidden after a successful commit). Returns ``None`` when the
    click resolves to the whole face or cannot be resolved confidently --
    the caller then keeps today's whole-face behavior.

    Inner regions are ``common(face, splitter_face)`` per splitter (a
    splitter overlapping the face edge is clipped by the common, probed);
    the outer region is ``face.cut(all splitter faces)``, which may be
    several disconnected faces (a band across the face) -- each is its own
    region. The clicked region is the one whose face contains ``point``
    (``distToShape``; a region's CenterOfMass can lie outside it, so COM
    is never used).
    """
    probe = Part.Vertex(point)
    regions = []  # (region_face, used_objects)
    overlap_faces = []
    used_all = []
    for obj, faces in splitters:
        contributed = False
        for sf in faces:
            try:
                overlap = face.common(sf)
            except Exception:
                continue
            for rf in overlap.Faces:
                if rf.Area < REGION_MIN_AREA:
                    continue
                regions.append((rf, [obj]))
                contributed = True
            overlap_faces.append(sf)
        if contributed:
            used_all.append(obj)
    if not regions:
        return None
    try:
        remainder = face.cut(overlap_faces)
        for rf in remainder.Faces:
            if rf.Area >= REGION_MIN_AREA:
                regions.append((rf, used_all))
    except Exception:
        pass

    best = None
    for rf, used in regions:
        try:
            d = rf.distToShape(probe)[0]
        except Exception:
            continue
        if d <= tol and (best is None or d < best[0]):
            best = (d, rf, used)
    if best is None:
        return None
    region = best[1]
    if region.Area >= face.Area - max(REGION_MIN_AREA, face.Area * 1e-9):
        return None  # a splitter covering everything: still the whole face
    return region, best[2]


def back_face_distances(face, shape, normal, tol=1e-6):
    """Sorted positive distances (mm) from ``face`` to the candidate "back
    faces" of ``shape`` along the inward ``-normal`` axis -- the depths at
    which an inward push breaks through to the opposite side (or into a
    void). Pure geometry, no document access; returns [] when there is
    nothing behind the face (open shell, standalone face).

    Two probed pitfalls shape the implementation:
      * the ray start must be a REAL on-face point: a holed face's
        CenterOfMass can sit INSIDE the hole (probed: a section ray cast
        from there crosses nothing), so a surface point is
        rejection-sampled from ParameterRange with ``isPartOfDomain``;
      * antiparallel planar faces are also collected by plane distance, so
        a back face the single sampled ray happens to miss still registers.
    """
    rng = random.Random(0)  # deterministic sampling, headless-reproducible
    umin, umax, vmin, vmax = face.ParameterRange
    point = None
    for _ in range(200):
        u = rng.uniform(umin, umax)
        v = rng.uniform(vmin, vmax)
        if face.isPartOfDomain(u, v):
            point = face.valueAt(u, v)
            break
    if point is None:
        point = face.CenterOfMass

    dists = []
    for g in shape.Faces:
        if not is_planar_face(g):
            continue
        try:
            gn = face_normal(g)
        except Exception:
            continue
        if gn.dot(normal) > -0.999:
            continue
        d = point.sub(g.CenterOfMass).dot(normal)
        if d > tol:
            dists.append(d)

    # line section from just under the sampled point: void-aware crossings,
    # including curved back faces the plane filter cannot see
    try:
        length = shape.BoundBox.DiagonalLength + 1.0
        line = Part.makeLine(point.sub(normal * 0.001), point.sub(normal * length))
        for vx in shape.section(line).Vertexes:
            d = point.sub(vx.Point).dot(normal)
            if d > tol:
                dists.append(d)
    except Exception:
        pass

    dists.sort()
    merged = []
    for d in dists:
        if not merged or d - merged[-1] > 1e-6:
            merged.append(d)
    return merged


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
