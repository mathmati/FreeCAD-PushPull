# SPDX-License-Identifier: MIT
"""Pure vector-math helpers for PushPull. No FreeCAD document/Gui state.

These functions take/return FreeCAD.Vector but do not touch the active
document, so they are trivially unit-testable outside a live FreeCAD
session too (only App.Vector is needed, which is available via freecadcmd
or the FreeCAD Python module).
"""


def closest_point_param_on_line_to_ray(line_origin, line_dir, ray_origin, ray_dir):
    """Return the scalar parameter ``s`` such that

        line_origin + s * line_dir

    is the point on the infinite line through ``line_origin`` (direction
    ``line_dir``) that is closest to the infinite ray/line through
    ``ray_origin`` (direction ``ray_dir``).

    This is the standard closest-point-between-two-3D-lines solution
    (minimizing the squared distance between the two parametrized lines).
    Used to turn a 2D mouse position (already unprojected to a 3D pick ray
    by the caller, e.g. via View3DInventorViewer.getPoint()+camera position)
    into a 1D drag distance along a face's normal, without needing the
    mouse to stay exactly on a plane.

    If ``line_dir`` has unit length, ``s`` is the drag distance in model
    units directly.
    """
    d1 = line_dir
    d2 = ray_dir
    r = line_origin.sub(ray_origin)
    a = d1.dot(d1)
    b = d1.dot(d2)
    c = d2.dot(d2)
    d = d1.dot(r)
    e = d2.dot(r)
    denom = a * c - b * b
    if abs(denom) < 1e-9:
        # Degenerate (ray parallel to the drag axis): fall back to
        # projecting the ray origin onto the line directly.
        if a < 1e-12:
            return 0.0
        return -d / a
    return (b * e - c * d) / denom
