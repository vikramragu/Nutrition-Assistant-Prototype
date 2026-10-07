# Design reference

Input material for the UI redesign. **Nothing in here ships** — it is what the real
components in `frontend/app/` are built *from*, not code that runs.

## Where to put things

```
design/
  README.md           <- this file
  stitch/
    design.md         <- the written spec: palette, type, spacing, component notes
    screen.png        <- the rendered design
    code.html         <- the generated markup, probably Tailwind
```

Keep the filenames as they came out of the zip. If more screens arrive later — a light-mode
variant, a refusal state, an empty state — add them alongside with descriptive names
(`screen-dark.png`, `screen-refusal.png`) rather than overwriting.

If `code.html` turns out to reference sibling files (a `css/` folder, images, fonts), put
those in `design/stitch/` too and leave the relative paths alone, or it will not render.

## What happens to it

The export is almost certainly Tailwind or plain HTML. The app uses CSS Modules, so the
design gets **translated** rather than dropped in: colours, spacing, radii, type scale and
layout are read out of the export and rewritten as CSS Modules against the existing
components. That keeps the dependency count at zero and keeps one styling system in the
codebase instead of two.

## What must survive the redesign

These are acceptance criteria, not preferences
([implementation-plan.md Phase 2.7](../docs/features/rag-sourced-claims/implementation-plan.md)).
A new look is free to change anything *except*:

- **Two documents read as two documents.** Separate blocks, each headed by publisher and
  year, with real space between them. Visual merging would undo the one-call-per-document
  guarantee the backend is built around.
- **Four states stay tellable apart** — answer, policy refusal, coverage refusal, error —
  by shape and wording as well as colour, so the distinction survives greyscale and
  red-green colour blindness. The two refusals mean opposite things: *the assistant will
  not* versus *the corpus does not*.
- **Every claim shows document name, publisher, year and a working link.**
- **The sources panel is never empty while an answer is selected.**

## Not in the backend

If the design includes these, they are not styling work:

| In the design | Needs |
|---|---|
Chat history sidebar, "New chat" | a `GET /conversations` list endpoint, which does not exist |
Delete a conversation | a delete endpoint, which does not exist |
"General knowledge" fallback answers | a deliberate reversal of the grounding rule — the pipeline refuses instead |

## Tracked?

`stitch-export/` and `screenshots/` are gitignored — a generated export is an input, and
this repo keeps its diffs reviewable. If a particular screenshot is worth keeping as a
record of what was agreed, add it with `git add -f`.
