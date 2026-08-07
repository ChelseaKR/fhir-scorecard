# 0000. Record architecture decisions

Status: Accepted

Date: 2026-08-07

Deciders: Chelsea Kelly-Reif

## Context

Architecturally significant decisions in this repository (what is graded, what is never probed,
how findings stay defensible) need a durable record of what was chosen, why, and what it costs.
Per the portfolio Documentation Standard, those decisions belong in ADRs rather than being
recoverable only from README prose, roadmap edits, or commit messages.

## Decision

Use Architecture Decision Records in `docs/adr/`, numbered sequentially with a four-digit
prefix. Each ADR records Status, Context, Decision, and Consequences, plus alternatives where
useful. An accepted ADR is append-only: a later decision that changes course adds a new ADR and
marks the old one `Superseded by NNNN` rather than rewriting history.

Any change to a grading guardrail (public-surface-only probing, the no-authentication rule,
fail-closed grading, spec-citation requirements, or a coverage/security threshold) must link an
ADR in its pull request.

## Consequences

Decisions are reviewable, diffable, and preserved with the code. Contributors spend a little
more time recording expensive-to-reverse choices, and the ADR index must remain sequential.
