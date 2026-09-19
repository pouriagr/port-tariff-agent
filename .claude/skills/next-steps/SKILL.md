---
name: next-steps
description: Report what is left to do in this repository. Use when the user asks what is next, what remains, where we left off, what the status is, or invokes /next-steps. Reads docs/roadmap.md, docs/decisions.md and docs/spec/ and reports open steps, pending decisions and any drift between docs and code.
allowed-tools: Read, Glob, Grep, Bash
---

# Next steps

Report the remaining work. This skill is read-only: it never edits files, never ticks
roadmap items and never starts implementing. It ends with a report.

## Procedure

1. **Read the roadmap.** Read `docs/roadmap.md` in full. It is the source of truth for
   status. Collect every item that is `[ ]` (not started) or `[~]` (in progress), keeping
   the phase heading each item belongs to.
2. **Find the active phase.** The active phase is the earliest phase that still has an
   unticked item. Everything after it is "later".
3. **Read the spec for the active phase.** The phase heading names its spec file under
   `docs/spec/`. Read it, so the open items can be described in terms of what the spec
   actually asks for rather than the one-line roadmap wording.
4. **Collect pending decisions.** These are two things:
   - Roadmap items whose text asks for a decision (wording such as "Decide", "ADR",
     "choose", "pick"). Each one is a decision the user has to make, not code to write.
   - Anything in `docs/spec/` marked as open, TBD, unresolved or "to be decided".
   Cross-check `docs/decisions.md` so a decision already recorded as an ADR is not
   reported as pending.
5. **Check for drift.** Compare the roadmap against the working tree, cheaply:
   - List `src/port_tariff_agent/` and `tests/` to see which modules already exist.
   - Run `git log --oneline -15` and `git status --short`.
   Report only real mismatches: a ticked item with no code or tests behind it, or code
   on disk for an item still marked `[ ]`. If nothing is inconsistent, say the roadmap
   matches the tree in one line and move on. Do not list files that match expectations.
6. **Check the definition of done for in-progress work.** If any item is `[~]` or the
   tree has uncommitted changes, note which of the four gates in `CLAUDE.md` still have
   to pass: tests covering the new code, `uv run pytest`, `uv run ruff check .` and
   `uv run ruff format --check .`, roadmap updated. Do not run these commands as part of
   this skill; only name the gates.

## Report format

Write the answer as prose and short lists, in English, under about 400 words.

- **Open with one line** naming the active phase and how many items remain in it.
- **Next up.** The next two or three roadmap items in order, each one sentence saying
  what the work actually is according to the spec. This is the main part of the report.
- **Decisions to make.** Only if there are any. One line each: the question, and the
  options if the docs or ADRs already name them. Say which phase blocks on it.
- **Drift.** Only if step 5 found a real mismatch.
- **Later phases.** One line per remaining phase with its count of open items. No detail.

Do not repeat the full roadmap back to the user. Do not propose an implementation plan
unless asked. End the report without offering to start the work.
