# FreeCAD PushPull

SketchUp-style direct modeling for FreeCAD: click a planar face on a
PartDesign Body, drag along its normal, and release to extrude/cut it --
with a live ghost preview and a numeric readout while you drag, and a
click-then-type precision path (type a number, press Enter) for exact
distances. Under the hood it commits a completely ordinary, parametric
`PartDesign::Pad` (dragging outward) or `PartDesign::Pocket` (dragging
inward) -- the same object types, editable afterward the same way, as if
you'd built them by hand.

## Why

FreeCAD's PartDesign workbench is a real parametric solid modeler, but
getting from "I want this face 10mm taller" to a result takes a sketch
reference, a Pad/Pocket dialog, and a typed length -- several steps for
something SketchUp, Fusion 360's "Press Pull", and SolidWorks' Instant3D
all do as a single click-drag gesture. This addon closes that specific
gap without touching FreeCAD's kernel or its existing feature types: it's
a thin, focused UI layer over the Pad/Pocket API that already exists.

**No equivalent addon was found in the FreeCAD Addon Manager index as of
2026-07** (a dedicated prior-art search -- WebSearch/WebFetch/GitHub code
search) turned up zero matches for "push pull" + workbench/addon,
`SoDragger`+freecad, and similar queries. This is a search result, not a
"first ever" or "only" claim -- an addon like this could exist and simply
not have turned up.

### Design456's pivot, disclosed honestly

The one serious prior attempt at this problem is
[MariwanJ/Design456](https://github.com/MariwanJ/Design456), whose README
explicitly names the same goal ("click a face and extrude/push-pull by
moving the mouse instead of typing a distance"). As of a 2026-07 prior-art
research (mid-2026), that project had **pivoted away from FreeCAD's
OCCT/Part kernel entirely toward a mesh-based engine**, with the author
citing difficulty making direct modeling work reliably on top of
FreeCAD's Part/PartDesign boolean operations. We take that as real,
useful signal about where the difficulty in this problem actually lives
-- not a reason to avoid it, but a reason to make one specific design
choice deliberately (see "No live OCCT recompute" below), which is our
best understanding of what the natural, naive approach gets wrong.

## What it does (v1 scope)

1. Activate the **Push/Pull** command (toolbar/menu, in its own
   workbench). If a planar face on a `PartDesign::Body`'s tip solid is
   already selected the normal FreeCAD way (click-selection), the drag
   arms immediately; otherwise click a planar face in the 3D view.
2. Move the mouse (holding the button, or -- SketchUp-style -- click
   once and move freely without holding) to drag along the face's
   normal. A translucent Coin3D ghost tracks the live distance, along
   with a status-bar readout (`PushPull: Pad 6 mm (Enter=commit,
   Esc=cancel)`).
3. **Type a number instead** (digits, `.`, `-`) at any point for the
   precise/click-then-type path -- the ghost/readout follow the typed
   value live too.
4. Release the drag (or click a second time, or press Enter) to commit:
   - Dragging **outward** (away from the solid) commits a
     `PartDesign::Pad` with `Length` = the drag distance.
   - Dragging **inward** commits a `PartDesign::Pocket` with `Length` =
     the (positive) drag distance.
   - Both use the picked face **directly as the Profile** --
     `Profile=(feature, ['FaceN'])` -- no Sketch object is created, and
     no data is duplicated; this is documented, current FreeCAD Pad/
     Pocket behavior, not a workaround.
   - Picking a **standalone drawn face** (a loose planar `Part` face that
     belongs to no Body -- exactly what the companion **SketchLayer**
     addon, or Draft, produces) instead commits a parametric
     `Part::Extrusion` into a solid. This is the SketchUp loop: *draw a
     rectangle, then push it up into a box.* The extrusion stays editable
     (`LengthFwd`) like a Pad.
5. **Esc** at any point cancels cleanly: the Coin ghost is removed, all
   event callbacks/filters are torn down, and the document is left
   completely unchanged.

### Guards (friendly messages, not crashes)

- Non-planar face picked -> "PushPull only supports planar faces (this
  one is curved)."
- Face on a bare **non-Body solid** (e.g. a `Part::Box`) -> a friendly
  message. Pushing an existing solid's face in place needs a boolean,
  which is still out of scope; use a PartDesign Body for that, or draw a
  loose face. (A *standalone* loose face is accepted -- see step 4's
  `Part::Extrusion` path above; only faces belonging to an existing bare
  solid are declined.)
- Drag distance too small (effectively a no-op) -> rejected, no feature
  created.
- A defensive re-check at commit time compares the picked face's area
  and center of mass against what `FaceN` resolves to on the feature at
  commit time; a mismatch aborts the commit with a message rather than
  silently padding/pocketing the wrong geometry (see "Toponaming" below
  -- this is a sanity check, not a fix for the underlying problem).

## No live OCCT recompute during the drag

This is the one design decision that matters most, and it's a direct
response to what the Design456 story suggests went wrong for the natural
approach: **the live drag preview never touches the document or the
OCCT kernel.** At drag-start, the picked face's outline and a coarse
tessellated fill are captured *once* (`Part.Face.tessellate()` /
`.OuterWire.discretize()`) and built into a small Coin3D scene-graph
node (mirroring the pattern core Draft's own
`draftguitools/gui_trackers.py` uses for its rubber-band previews). Every
subsequent mouse-move tick only updates that node's `SoTransform`
translation -- a cheap, constant-time GPU-side operation regardless of
model complexity. The real `PartDesign::Pad`/`Pocket` feature, and the
one-time `Document.recompute()` it triggers, is only created **once, on
release** (or Enter). This is also how Fusion 360/SolidWorks' own "live"
previews actually behave -- their in-progress ghost is a cheap tessellated
preview too, not a live re-solve of the whole feature tree.

## Verification method (what was and wasn't synthesized)

Built and verified against a real, installed FreeCAD 1.1 (not a mock),
two ways:

1. **Headless (`freecadcmd`)** -- `verify/headless_regression.py` drives
   `PushPullController` (the click-drag(-type)-commit state machine)
   directly by method call: builds a `PartDesign::Body` + base Pad,
   picks its top face, types a distance and commits (`type_char`/
   `key_return`) -> asserts a real parametric `PartDesign::Pad` exists
   with the exact typed length and `Body.Tip` advanced to it; a second
   pass simulates a negative drag distance -> asserts a
   `PartDesign::Pocket`. Also covers the guards above and a cancel path
   that leaves the document untouched. 21/21 checks pass.
2. **GUI, under Xvfb** -- `verify/drivers/pushpull_drag_driver.py` and
   `pushpull_commit_driver.py` invoke the *real* `PushPullCommand` class
   (`Activated()`, exactly what a toolbar click runs) against a real 3D
   view, using the real (non-simulated) `Gui.Selection` API for the face
   pick, and capture screenshots showing (a) the live ghost + status-bar
   readout mid-drag and (b) two real committed Pads in the tree/3D view
   (one via a simulated drag, one via genuinely synthetic Qt keyboard
   input for the click-then-type path).

**Testing note on synthetic input**, since this is the part most
worth being precise about: synthesizing genuine Coin3D/SoEvent
mouse-drag events (a picking ray through a specific pixel actually
hitting geometry via `SoRayPickAction`, then live `SoLocation2Event`
deltas) under a headless Xvfb X server was **not attempted** -- doing
that convincingly needs a real windowing/input path this environment
doesn't have, and it's the well-known hard part of testing 3D-viewport
interaction. A scripted fallback is used for that part instead: the real command is invoked, the
real `Gui.Selection` API supplies the face pick, and a mouse-move tick is
simulated with a **direct call** to `controller.update_distance(...)` --
the exact method the real `SoLocation2Event` handler calls after
unprojecting a pixel to a 3D ray. The keyboard path, by contrast, **is**
exercised with genuinely synthetic input: real `QKeyEvent` objects
dispatched via `QApplication.sendEvent` through the actual installed
event filter -- not a fallback. (One real bug surfaced building this:
FreeCAD binds bare digit keys 0-6 to "set standard view" shortcuts,
which raced ahead of a plain `QEvent.KeyPress` handler and silently
swallowed every digit; fixed by also handling `QEvent.ShortcutOverride`
to claim the key first -- see `commands.py:_KeyFilter`.)

## Toponaming

Referencing a face by name (`Face7`) and using that reference later is
exactly FreeCAD's topological-naming-problem failure mode: face indices
can shift on recompute (FreeCAD/FreeCAD#8432, #17041), and FreeCAD 1.0+'s
stable-naming work is a real improvement but explicitly not a complete
fix. **PushPull does not attempt to solve this** -- it uses the same
face-as-Profile mechanism (`Profile=(feature, ['FaceN'])`) that a human
building a Pad/Pocket by hand in the GUI already uses, so it carries
exactly the toponaming exposure core PartDesign already has today, no
more and no less. The one thing it adds is a cheap defensive check (area
+ center-of-mass comparison) at commit time that aborts loudly instead of
silently committing against the wrong face if something shifted between
pick and commit -- a sanity check, not a fix.

## Requirements

FreeCAD 1.1+, a `PartDesign::Body`. No third-party Python dependencies
beyond what FreeCAD itself ships (`pivy`, `PySide`).

## Known gaps (disclosed up front)

- No in-place push/pull of a face on an existing bare (non-Body) solid --
  that needs a boolean and is still out of scope (a *standalone* loose
  face is extruded; a face of an existing bare solid is declined with a
  friendly message).
- No rectangle-on-face-to-new-sketch mode (SketchUp's "draw a shape on a
  face" gesture) itself -- but you can now *draw* the face to push/pull
  with FreeCAD's Draft workbench (whose snapping is itself SketchUp-
  inspired) or the companion **SketchLayer** addon, then Push/Pull the
  resulting loose face into a solid.
- Only one face can be dragged per command activation; re-activate (or
  re-invoke) to push/pull another face.
- Not internationalized (UI strings are plain Python, not
  `QT_TRANSLATE_NOOP`-wrapped).
- The application-level Qt key filter intercepts digit-key view
  shortcuts while a drag session is open (see "Verification method"
  above) -- expected and short-lived (Esc/commit end the session), but
  worth knowing if it's ever surprising.

## License

MIT, see `LICENSE`.

## Transparency

Built with [Claude Code](https://claude.com/claude-code).
