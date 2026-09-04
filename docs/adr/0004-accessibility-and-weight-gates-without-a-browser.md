# 0004. Accessibility and transfer-size gates that a static reader can decide

## Status

Accepted - 2026-08-27

## Context

`ROADMAP.md` phase 4 asked for "Lighthouse and accessibility budgets as merge gates, matching
the 100-accessibility bar held elsewhere in the portfolio", and the README's two relevant rows
described the state honestly: Accessibility said "formal assistive-technology review not yet
performed", and Performance said "no transfer-size or timing budget is enforced in CI and none
is claimed".

Taking that literally would mean running Lighthouse in CI and failing the build on its score.
Three things are wrong with that here.

**A Lighthouse score is not a pass or a fail.** Its accessibility category is a weighted
average of audits, most of them axe-core rules, and a page can lose points for something no
criterion requires or hold 100 while failing a criterion no automated rule can see. A gate
whose threshold is a weighted average of a moving rule set is a gate whose meaning changes when
the tool updates, which is the opposite of what every other gate in this repository does.

**It is not deterministic.** The performance half of the score depends on CPU contention on the
runner. `make verify` is byte-for-byte identical locally and in CI by design, and the release
process re-runs it at a signed commit; a step that returns a different number on a busy runner
cannot join it.

**It requires a browser and an npm toolchain.** This package has no runtime dependencies, its
dev group is a PEP 735 group that `make audit` scans in full, and `make verify` runs offline
after `make sync`. Adding Node, a lockfile no `pip-audit` covers, and a headless Chrome
download to the merge gate is a large, unaudited supply-chain surface for a static site of
plain semantic HTML.

## Decision

Gate on what a static reader can decide, and say plainly what that leaves out.

`fhir_scorecard.accessibility` implements twelve rules over the built HTML. **Seven name the
WCAG 2.2 Level A success criterion they implement:** language of page (SC 3.1.1), page titled
(SC 2.4.2), alt text present on every image (SC 1.1.1), an accessible name on every control and
every link (SC 4.1.2, SC 2.4.4), and id references and same-page fragments that resolve
(SC 1.3.1, SC 2.4.1).

**Five are this project's own rule rather than a criterion, and each says so where it is
defined:** unique page titles, no duplicated id, a top-level heading, no skipped heading level,
and a main landmark.
A rule may cite a criterion only where the criterion requires what the rule reports, which is
the standard the grading rules are already held to. It does not hold for these four. SC 2.4.2
requires a title that describes the topic, not a unique one. SC 4.1.1 Parsing, which duplicate
ids used to fall under, was removed in WCAG 2.2. SC 1.3.1 asks that structure conveyed through
presentation be programmatically determined, which an h1 followed by an h3 already is, so no
Level A criterion requires sequential heading levels. The same sentence disposes of requiring an
h1 at all: G141 offers headings as one *sufficient technique* for satisfying SC 1.3.1, which is
not the same as the criterion requiring a top-level heading. And SC 2.4.1 Bypass Blocks asks for
a mechanism that bypasses repeated blocks, which a skip link provides whatever it points at, so
no Level A criterion requires a `main` landmark. All five are still worth checking on a site
generated from one template set, where each is a generator defect rather than an authoring
choice; they are checked under this project's own name.

`fhir_scorecard.weight` enforces two transfer-size budgets: one on each page's own bytes,
including any subresource no other page links, and one on the subresources more than one page
links, counted once. Both numbers were measured from the published site on 2026-08-27 rather
than chosen, and the module records the measurement beside the budget.

Both run inside `fhir-scorecard audit-site`, which the test suite runs against a
fixture-built site and the publish workflow runs against the real build before it is uploaded.
All three families always run together; there is no flag to skip one.

## What this does not do, stated because a green gate is read as a claim

A browser would catch, and this does not:

- **Colour contrast as rendered.** Deciding it needs the cascade, the computed colours of an
  element and its actual backdrop, and the rendered font size. The site is styled by the
  vendored U.S. Web Design System, whose palette is designed against WCAG contrast ratios, but
  that is an inherited property this repository does not measure.
- **Focus order and visible focus.** Both depend on layout and on the user agent.
- **Computed ARIA roles.** These rules read the markup as written. A role that changes an
  element's accessible name computation is not modelled.
- **Reflow at 320 CSS pixels, text spacing, and motion** (SC 1.4.10, 1.4.12, 2.3.3). All need
  rendering.
- **Whether a name is any good.** `A11Y_LINK_WITHOUT_TEXT` passes a link labelled "click
  here". SC 2.4.4 asks whether the purpose is clear, which is a judgment.

Above all, **this is not the assistive-technology review**. That review stays open in
`docs/RESPONSIBLE-TECH-AUDITS.md` section E, and section E now says which half of it this
closed rather than being rewritten as though it closed both. A page can satisfy every rule here
and be unusable with a screen reader.

## Consequences

The README's Accessibility row can say an automated gate exists and name what it covers; it
still cannot say the site has been reviewed with assistive technology, and it does not. The
Performance row can name a transfer-size budget; timing stays unclaimed, because there is no
server-side surface to time and a wall-clock number from a CI runner is a fact about the
runner.

If a browser-driven check is ever wanted, the place for it is a separate non-gating job whose
output is evidence for the open review, not a merge gate. This decision is about what may block
a merge, not about what is worth measuring.

## Amendment, 2026-09-04: the h1 rule was reclassified

`A11Y_NO_TOP_LEVEL_HEADING` shipped citing WCAG 2.2 SC 1.3.1, and the decision above counted it
among the criterion-backed rules. It is not one. SC 1.3.1 asks that structure conveyed through
presentation be programmatically determinable; G141 offers headings as one *sufficient
technique* for that, which is not the same as the criterion requiring a top-level heading. No
Level A criterion does.

This was already the argument two paragraphs above, made about `A11Y_HEADING_LEVEL_SKIPPED` and
`A11Y_NO_MAIN_LANDMARK` and applied to demote both. It applies identically here and was not
applied, so the split shipped as eight and four when the rules it describes are seven and five.

Nothing about the gate's behaviour changes: the rule fires on exactly the same pages, and it is
still worth keeping for exactly the reason the other own-rules are kept, since every page here
is generated from one template set that always emits exactly one h1. What changes is the
sentence a reader of the finding sees, and the counts in this ADR, the README, the ROADMAP and
`docs/RESPONSIBLE-TECH-AUDITS.md`. Those counts are now recomputed from `A11Y_CODES` and
asserted against each of those documents, so the next drift fails the build rather than waiting
for a reader.
