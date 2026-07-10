PREPARED DRAFT — awaiting user review and sign-off before any external submission.

# AI-Assistance Disclosure — FreeCAD PushPull addon

This document is the standing AI-assistance disclosure for the FreeCAD
PushPull addon, written to satisfy FreeCAD's
[`AI_POLICY.md`](https://github.com/FreeCAD/FreeCAD/blob/main/AI_POLICY.md)
(effective June 29, 2026). It is meant to be:

- linked from the addon's `README.md` (already done),
- pasted, adapted, into the description of any PR or Issue this addon's
  maintainer opens against FreeCAD-adjacent repositories, and
- kept accurate — update it if the tooling or process changes.

## Natural-language disclosure (paste into PR/issue descriptions)

> This contribution was drafted with the assistance of an AI coding
> assistant (Claude, by Anthropic). All code, data, documentation, and
> copy were reviewed and are fully understood by the named human
> maintainer below, who takes personal responsibility for this
> contribution and personally handles all reviewer communication on it —
> no AI-generated replies are posted under this account. This complies
> with FreeCAD's AI_POLICY.md disclosure requirement.

## Git trailer (append to every prepared commit)

```
Assisted-by: Claude (Anthropic, Sonnet 5)
```

Adjust the model identifier to match whichever Claude model actually
produced the change, per `AI_POLICY.md`'s
`Assisted-by: [Model-Family] ([Version/ID])` format.

## PR-template checkbox

FreeCAD's PR template requires checking:

> "This PR is not unverified AI output, I take responsibility for it, and
> all communication from my side in this PR is done by me personally."

Do not check this box, and do not open the PR, until
mathmati has personally reviewed the diff, tested it
against a live FreeCAD install, and is prepared to answer reviewer
questions personally (not by relaying an AI's answers verbatim).

## What "human-reviewed" needs to mean in practice for this addon

This session performed (and this disclosure records honestly what was and
was not done):

- **Done, this session:** a clean install (fresh copy, not a symlink,
  matching what the Addon Manager does) into FreeCAD's real
  `~/.local/share/FreeCAD/v1-1/Mod/` under FreeCAD 1.1.0, confirmed the
  workbench auto-registers. A headless (`freecadcmd`) regression of the
  full click-drag(-type)-commit state machine (21 checks: Pad commit via
  typed distance, Pocket commit via simulated drag distance, pick
  resolution via both the tip feature and the wrapping Body object, and
  four guard/cancel paths) — all passing. A GUI verification under Xvfb
  driving the real `PushPullCommand` class end-to-end: a live-drag
  screenshot (Coin ghost + status-bar readout) and a committed-features
  screenshot (two real `PartDesign::Pad` objects in the tree, one
  committed via a simulated mouse-drag distance, one via genuinely
  synthetic Qt keyboard input for the click-then-type precision path).
  One real bug was found and fixed in the process: FreeCAD's built-in
  digit-key view shortcuts (0-6) raced ahead of a naive `QEvent.KeyPress`
  handler and silently ate every typed digit; fixed by also handling
  `QEvent.ShortcutOverride` to claim the relevant keys first.
- **NOT yet done — requires the named human maintainer before
  submission:** synthesizing true Coin3D/SoEvent mouse-drag events (a
  picking ray through a specific pixel actually hitting geometry, with
  live `SoLocation2Event` deltas) was not attempted under Xvfb — this
  session used the sanctioned fallback of calling the drag state
  machine's own methods directly instead (see the README's "Verification
  method" section for the full honest breakdown of what was and wasn't
  synthesized). A human should ideally still verify the live-drag *feel*
  (mouse sensitivity, ghost responsiveness) with a real mouse on a real
  display before wide release. Also not done: a live multi-platform
  install test (Windows/macOS Mod paths were not exercised, only Linux);
  a Part::Extrude fallback for non-Body faces (deliberately out of v1
  scope, disclosed in the README); and all the human-only steps that
  would normally live in a `RELEASE_CHECKLIST.md` (maintainer contact
  info, repository creation, version-tag discipline).

## Prior art and heritage disclosure

A dedicated prior-art search was performed before this addon was
considered release-quality (2026-07-10, `ops/scout-pushpull.md` in the
build record, live WebSearch/WebFetch/GitHub code search only, no git
commands, no sub-agents). It found no working push/pull addon in the
FreeCAD Addon Manager index as of that search, and one serious prior
attempt, **MariwanJ/Design456**, whose README states the same goal but
which had pivoted away from FreeCAD's OCCT/Part kernel toward a
mesh-based engine, citing difficulty making direct modeling reliable atop
FreeCAD's Part/PartDesign booleans. This addon is positioned in its
README as a response to that signal (a specific "no live OCCT recompute
during the drag" design choice), not as a claim of being the only or
first such tool, and Design456 is credited by name as the prior serious
attempt at this exact problem.

## Status

This addon has **not yet been submitted anywhere**. This disclosure is
prepared in advance so that whenever the maintainer does open the Index
issue, a PR, or a forum post, the disclosure language is already reviewed
and ready to attach — not written under time pressure at submission time.
