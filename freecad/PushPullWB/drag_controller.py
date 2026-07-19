# SPDX-License-Identifier: MIT
"""PushPullController: the click-drag-commit state machine.

Deliberately decoupled from raw Coin/pivy event objects so it can be
driven two ways with *identical* code paths:

  1. From commands.py's real SoEvent/Qt callbacks in the live GUI.
  2. Directly, by method call, from a headless (freecadcmd) test or a
     GUI-driven verification script -- simulating "the user moved the
     mouse N mm along the normal" as ``controller.update_distance(N)``
     and "the user typed 12 and pressed Enter" as
     ``controller.type_char('1'); controller.type_char('2');
     controller.key_return()``.

No OCCT/document recompute happens until :meth:`commit` -- every mouse-move
tick only updates the Coin ghost's SoTransform (see tracker.py) and, if a
view is attached, a status-bar text readout. This is the "cheap ghost, not
live OCCT" design a prior-art review flagged as the difference between a
usable tool and the trap that stalled Design456's push/pull attempt.
"""
from . import commit as commit_mod
from . import face_utils

try:
    from . import tracker as tracker_mod
except Exception:  # pragma: no cover - pivy/Coin unavailable (headless import)
    tracker_mod = None


class PushPullController:
    """One instance per in-progress (or just-finished) PushPull drag.

    ``view`` is the live ``Gui.ActiveDocument.ActiveView`` when driven from
    the real GUI, or ``None`` for headless use (in which case no Coin
    ghost/status-bar text is created -- pure state-machine bookkeeping).
    """

    #: pixel movement (in either axis) below which a mouse-up is treated as
    #: "just a click to arm" rather than "the end of a drag" -- lets the
    #: SketchUp-style click / move-without-holding / click-again gesture
    #: and the click-then-type gesture both work.
    DRAG_PIXEL_THRESHOLD = 4

    #: an interactive inward drag within this many mm of the nearest
    #: back-face candidate snaps onto it (and can never pass it) --
    #: SketchUp's "offset is limited" stop at the opposite face. Landing an
    #: interactive push epsilon-short of the back face would otherwise
    #: leave a ~1e-7 membrane (probed). Typed distances bypass this.
    BACK_SNAP_MM = 0.5

    def __init__(self, doc, view=None):
        self.doc = doc
        self.view = view
        self.reset()

    def reset(self):
        # A ghost from an interrupted session must leave the scene graph
        # before its reference is dropped, or it stays visible forever.
        ghost = getattr(self, "ghost", None)
        if ghost is not None:
            try:
                ghost.remove()
            except Exception:
                pass
        self.active = False
        self.body = None
        self.feature = None
        self.standalone = False
        self.face_name = None
        self.origin = None
        self.normal = None
        self._expected_area = None
        self._expected_com = None
        self._back_candidates = []
        self.distance = 0.0
        self.typed_buffer = ""
        self.committed_object = None
        self.last_message = ""
        self.ghost = None

    # -- pick / start ------------------------------------------------
    def start(self, obj, sub_name):
        """Validate and begin a drag from a (obj, sub_element_name) pick,
        e.g. straight from Gui.Selection or a preselection callback.

        Returns (True, "ok") on success, or (False, message) on a friendly
        rejection (non-planar face, face not on a Body, etc.) -- the caller
        should show ``message`` to the user (status bar / Report View) and
        NOT enter drag mode.
        """
        try:
            pick = face_utils.validate_pick(obj, sub_name)
        except face_utils.FaceRejected as exc:
            self.last_message = str(exc)
            return False, self.last_message

        self.reset()
        self.body = pick["body"]
        self.feature = pick["feature"]
        self.standalone = pick["standalone"]
        self.face_name = pick["face_name"]
        self.origin = pick["origin"]
        self.normal = pick["normal"]
        self._expected_area = pick["face"].Area
        self._expected_com = pick["face"].CenterOfMass
        # back-face candidate depths for the push-through clamp, computed
        # once here at drag start (one cheap OCCT section, never per tick)
        self._back_candidates = []
        if not self.standalone:
            try:
                self._back_candidates = face_utils.back_face_distances(
                    pick["face"], self.feature.Shape, self.normal)
            except Exception:
                self._back_candidates = []
        self.active = True
        self.distance = 0.0
        self.typed_buffer = ""

        if self.view is not None and tracker_mod is not None:
            self.ghost = tracker_mod.FaceGhostTracker(pick["face"])
            self.ghost.show()

        self._update_readout()
        return True, "ok"

    # -- live drag -----------------------------------------------------
    def update_distance(self, distance):
        """Update the live drag distance (mm, signed along the face
        normal). Cheap: only moves the ghost's transform / updates the
        status-bar text -- no OCCT call."""
        if not self.active:
            return self.distance
        distance = float(distance)
        # SketchUp's "offset is limited": an interactive inward drag stops
        # at the nearest back-face candidate, snapping onto it from
        # BACK_SNAP_MM away so the commit lands exactly through. Typed
        # distances are exact and skip this entirely (typed_buffer set).
        if distance < 0 and not self.typed_buffer and self._back_candidates:
            limit = self._back_candidates[0]
            if -distance >= limit - self.BACK_SNAP_MM:
                distance = -limit
        self.distance = distance
        if self.ghost is not None:
            self.ghost.set_offset(self.normal * self.distance)
        self._update_readout()
        return self.distance

    def update_from_ray(self, ray_origin, ray_dir):
        """Convenience wrapper: project a 3D pick ray (origin + direction,
        e.g. unprojected from a mouse position via the 3D view's camera)
        onto the drag axis (origin=self.origin, dir=self.normal) and update
        the live distance from the resulting parameter."""
        from . import geom

        s = geom.closest_point_param_on_line_to_ray(
            self.origin, self.normal, ray_origin, ray_dir
        )
        return self.update_distance(s)

    # -- typed precision path -------------------------------------------
    def type_char(self, ch):
        """Feed one typed character (digit, '.', or '-') into the typed
        distance buffer, live-updating the ghost/readout to match."""
        if not self.active:
            return
        if ch == "-":
            # Only meaningful at the start of the buffer (sign flip).
            if self.typed_buffer.startswith("-"):
                self.typed_buffer = self.typed_buffer[1:]
            else:
                self.typed_buffer = "-" + self.typed_buffer
        elif ch == "." and "." not in self.typed_buffer:
            self.typed_buffer += ch
        elif ch.isdigit():
            self.typed_buffer += ch
        else:
            return
        self._sync_typed_preview()

    def key_backspace(self):
        if not self.active or not self.typed_buffer:
            return
        self.typed_buffer = self.typed_buffer[:-1]
        self._sync_typed_preview()

    def _sync_typed_preview(self):
        if self.typed_buffer in ("", "-", ".", "-."):
            self._update_readout()
            return
        try:
            value = float(self.typed_buffer)
        except ValueError:
            return
        self.update_distance(value)

    def key_return(self):
        """Enter/Return: commit at the typed distance if one was typed,
        else at the current (mouse-driven) distance."""
        return self.commit()

    # -- commit / cancel -------------------------------------------------
    def commit(self):
        """Commit the drag as a real parametric Pad/Pocket. Returns the new
        feature object on success, or None (with ``self.last_message`` set)
        on a friendly failure -- in which case the document is left
        unchanged and the ghost/typing state is torn down either way."""
        if not self.active:
            return None

        distance = self.distance
        typed = False
        if self.typed_buffer not in ("", "-", ".", "-."):
            try:
                distance = float(self.typed_buffer)
                typed = True
            except ValueError:
                pass

        if not face_utils.face_still_matches(
            self.feature, self.face_name, self._expected_area, self._expected_com
        ):
            self.last_message = (
                "PushPull: the picked face changed since it was selected "
                "(toponaming shift or a concurrent edit) -- commit aborted "
                "for safety. Re-select the face and try again."
            )
            self._teardown()
            return None

        # An interactive push that stopped exactly at the LAST back-face
        # candidate (nothing left behind it) is a break-through: commit it
        # as Pocket Type='ThroughAll', never an epsilon-short Length (which
        # leaves a ~1e-7 membrane, probed). A clamped stop at a nearer
        # candidate with material beyond it stays an exact numeric Length.
        through = (
            not self.standalone
            and not typed
            and distance < 0
            and bool(self._back_candidates)
            and -distance >= self._back_candidates[0] - 1e-6
            and self._back_candidates[-1] - self._back_candidates[0] <= 1e-6
        )

        try:
            if self.standalone:
                new_obj = commit_mod.commit_seed_body(
                    self.doc, self.feature, self.face_name, self.normal, distance)
            else:
                new_obj = commit_mod.commit_pushpull(
                    self.doc, self.body, self.feature, self.face_name, distance,
                    through=through)
        except commit_mod.CommitError as exc:
            self.last_message = str(exc)
            self._teardown()
            return None

        self.committed_object = new_obj
        self.last_message = f"PushPull: committed {new_obj.TypeId} '{new_obj.Name}' ({distance:.3g} mm)."
        self._teardown()
        return new_obj

    def cancel(self):
        """Esc: abandon the drag, no document changes, clean teardown."""
        self.last_message = "PushPull: cancelled."
        self._teardown()

    def _teardown(self):
        if self.ghost is not None:
            self.ghost.remove()
            self.ghost = None
        self.active = False
        if self.view is not None:
            self._clear_readout()

    # -- status-bar readout ----------------------------------------------
    def _update_readout(self):
        if self.view is None:
            return
        try:
            import FreeCADGui as Gui

            if self.standalone:
                kind = "Extrude"
            else:
                kind = "Pad" if self.distance >= 0 else "Pocket"
            typed = f" [typed: {self.typed_buffer}]" if self.typed_buffer else ""
            limited = ""
            if (self.distance < 0 and not self.typed_buffer
                    and self._back_candidates
                    and -self.distance >= self._back_candidates[0] - 1e-6):
                is_through = (self._back_candidates[-1]
                              - self._back_candidates[0] <= 1e-6)
                limited = (" (offset limited: through)" if is_through
                           else " (offset limited)")
            msg = f"PushPull: {kind} {abs(self.distance):.3g} mm{typed}{limited}  (Enter=commit, Esc=cancel)"
            Gui.getMainWindow().statusBar().showMessage(msg)
        except Exception:
            pass

    def _clear_readout(self):
        if self.view is None:
            return
        try:
            import FreeCADGui as Gui

            Gui.getMainWindow().statusBar().showMessage(self.last_message, 5000)
        except Exception:
            pass
