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
search, no git commands, no sub-agents -- turned up zero matches for
"push pull" + workbench/addon, `SoDragger`+freecad, and similar queries;
see `ops/scout-pushpull.md` in the build record for the full method and
citations). This is stated as a novelty-search result, not a "first ever"
or "only" claim -- an addon like this could exist and simply not have
turned up in that search.

### Design456's pivot, disclosed honestly

The one serious prior attempt at this problem is
[MariwanJ/Design456](https://github.com/MariwanJ/Design456), whose README
explicitly names the same goal ("click a face and extrude/push-pull by
moving the mouse instead of typing a distance"). As of the scout's
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
5. **Esc** at any point cancels cleanly: the Coin ghost is removed, all
   event callbacks/filters are torn down, and the document is left
   completely unchanged.

### Guards (friendly messages, not crashes)

- Non-planar face picked -> "PushPull only supports planar faces (this
  one is curved)."
- Face not on a `PartDesign::Body` (e.g. a bare `Part::Box`) -> a
  friendly message. **v1 does not offer a Part::Extrude fallback** for
  this case (kept out of scope deliberately, per the feasibility scout's
  "keep v1 simple" recommendation) -- pick a face on a PartDesign part
  instead.
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

**Honest disclosure on synthetic input**, since this is the part most
worth being precise about: synthesizing genuine Coin3D/SoEvent
mouse-drag events (a picking ray through a specific pixel actually
hitting geometry via `SoRayPickAction`, then live `SoLocation2Event`
deltas) under a headless Xvfb X server was **not attempted** -- doing
that convincingly needs a real windowing/input path this environment
doesn't have, and it's the well-known hard part of testing 3D-viewport
interaction. Per this build's verification brief, the sanctioned
fallback is used for that part instead: the real command is invoked, the
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

## AI-assistance disclosure

This addon was built with the assistance of an AI coding assistant
(Claude, by Anthropic), reviewed and taken responsibility for by the
named human maintainer, per FreeCAD's `AI_POLICY.md`. Full disclosure
text and the `Assisted-by:` commit-trailer convention are in
`submission/DISCLOSURE.md` in the build record.

## Known gaps for v1.1 (disclosed up front)

- No `Part::Extrude` fallback for faces on bare (non-Body) `Part`
  solids -- friendly message only (deliberate v1 scope cut).
- No rectangle-on-face-to-new-sketch mode (SketchUp's "draw a shape on
  a face" gesture) -- this addon only pushes/pulls a face that already
  exists.
- Only one face can be dragged per command activation; re-activate (or
  re-invoke) to push/pull another face.
- Not internationalized (UI strings are plain Python, not
  `QT_TRANSLATE_NOOP`-wrapped).
- The application-level Qt key filter intercepts digit-key view
  shortcuts while a drag session is open (see "Verification method"
  above) -- expected and short-lived (Esc/commit end the session), but
  worth knowing if it's ever surprising.

## License

MIT, see `LICENSE-Code`.
