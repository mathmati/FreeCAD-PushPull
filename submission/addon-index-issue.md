PREPARED DRAFT — awaiting user review and sign-off before any external submission.

---

**Where this goes, when approved:** an issue of type "Addon - Addition" at
`https://github.com/FreeCAD/Addons/issues/new/choose` (or the Codeberg
mirror `https://codeberg.org/FreeCAD/Addons`), per the Addon-Academy
publishing guide.

**Before this can actually be filed:** the addon needs its own public repo
(currently it lives inside a private FableMax monorepo), a tagged release
on a stable `main`/`release` branch, a multi-platform (Windows/macOS) Mod
path sanity check (only Linux was exercised this session -- see
`submission/DISCLOSURE.md`), a live-mouse feel check on a real display
(the drag/ghost logic was verified via direct state-machine calls and
genuinely synthetic Qt keyboard input under Xvfb, not a real mouse -- see
the README's "Verification method" section), and every `[PLACEHOLDER]`
below filled in.

**Prior-art search performed:** a dedicated prior-art search (live
WebSearch/WebFetch/GitHub code search only, no git commands, no
sub-agents) was performed before this addon was considered
release-quality -- see `ops/scout-pushpull.md` (2026-07-10) for the full
method and evidence.

---

## Issue title

Addon Addition: FreeCAD PushPull

## Repository

`https://github.com/mathmati/FreeCAD-PushPull`
(branch: `main`)

## Description

**FreeCAD PushPull** brings SketchUp/Fusion-360-style direct modeling to
FreeCAD PartDesign: click a planar face on a Body's tip solid, drag along
its normal, and release to Pad (outward) or Pocket (inward) it -- with a
live Coin3D ghost preview and numeric readout while dragging, and a
click-then-type precision path (type a distance, press Enter) for exact
values. It commits an entirely ordinary, parametric `PartDesign::Pad`/
`Pocket` using the picked face directly as `Profile` (no Sketch object,
no duplicated data) -- documented, current FreeCAD API behavior, not a
workaround. The live drag preview never touches the document or the OCCT
kernel (a cheap, once-built Coin3D ghost only moves via `SoTransform` on
each mouse tick); the real feature and its one recompute happen only
once, on commit.

## Heritage and prior art

No working push/pull addon was found in the FreeCAD Addon Manager index
as of this search (2026-07). The one serious prior attempt at this exact
problem is **MariwanJ/Design456**, whose README states the same goal
("click a face and extrude/push-pull by moving the mouse instead of
typing a distance") but which, per its own README, pivoted away from
FreeCAD's OCCT/Part kernel toward a mesh-based engine, with the author
citing difficulty making direct modeling reliable atop FreeCAD's
Part/PartDesign boolean operations. We read that as real signal about
where the difficulty in this problem lives (a live-OCCT-recompute
approach doesn't scale to real drag-rate mouse events) and made the
"cheap ghost, commit-once" design choice specifically in response to it
-- not as a claim of being the only or first such tool. Design456 is
credited by name in the README as the prior serious attempt.

## How this addon meets the Qualities checklist

- **Governance:** named maintainer, `mathmati`
  (`177616452+mathmati@users.noreply.github.com`), actively watching issues/PRs on the
  repository above.
- **Compliance:** no network access at all -- this addon only reads/
  writes the active FreeCAD document and the 3D view.
- **Licensing:** code MIT (`package.xml` `<license>` + `LICENSE-Code`).
- **Codebase:**
  - Modern namespaced layout (`freecad/PushPullWB/`), no legacy
    `InitGui.py` shim, no `sys.path` manipulation.
  - Uses FreeCAD's provided Qt wrapper (`PySide`) exclusively.
  - `<classname>PushPullWorkbench</classname>` matches the registered
    `Gui.Workbench` subclass exactly; `GetClassName()` returns
    `"Gui::PythonWorkbench"`.
  - SVG-only icon (`Resources/Icons/pushpull.svg`); no compiled Qt
    resources.
  - No third-party Python dependencies beyond what FreeCAD itself ships.
- **Best practices:** version/date (`0.1.0` / `2026-07-10`) will be
  bumped on every change reaching the release branch; repository will
  carry the `freecad` and `addon` topics.

**Known gaps, disclosed up front rather than discovered in review:** no
`Part::Extrude` fallback for faces on bare (non-Body) `Part` solids
(friendly message only, deliberate v1 scope cut); no rectangle-on-face-
to-new-sketch mode; only one face can be dragged per command activation;
not internationalized; the application-level Qt key filter intercepts
FreeCAD's bare digit-key view shortcuts while a drag session is open
(short-lived, ends on Esc/commit). See the README's "Known gaps for v1.1"
section for the full list.

## AI-assistance disclosure

This addon was built with the assistance of an AI coding assistant
(Claude, by Anthropic), reviewed, tested, and taken responsibility for by
the named human maintainer above, per FreeCAD's `AI_POLICY.md`. Full
disclosure text and the `Assisted-by:` trailer convention used on this
project's commits are in the repository's `DISCLOSURE.md` (mirrors
`submission/DISCLOSURE.md` in the build record), including the honest
breakdown of what verification was and wasn't synthesized (real Coin3D
mouse-drag events were not synthesized under Xvfb; the drag state machine
was verified via direct method calls instead, per that document and the
README). All review responses on this issue and any follow-up PRs will
be written personally by the maintainer, not relayed AI output.

## Beta-testing note

Per the publishing guide's recommendation to beta-test with real users
before requesting indexing: [PLACEHOLDER — describe where this was
shared for real-user feedback, e.g. a FreeCAD forum PartDesign subforum
thread URL, or state that beta-testing is still pending and this issue
is filed as a work-in-progress/PoC per the guide's allowance for
clearly-marked WIP addons].
