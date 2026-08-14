# Willow's Rulebook (the "state machine")

This document is the fixed set of rules Willow uses to decide **what you hear**.
It exists because *grouping is not just presentation* — deciding what reaches the
user carries real inferential weight. So the decision must be
a **deterministic rule over event types already in the log**, not an AI prompt.
The same events always produce the same announcement. It is reviewable, and it
behaves the same across model versions.

This is a deliberate design decision, not an incidental one.

## The event types Willow knows about

All are **facts** built from *outside* the agent (so they are verifiable):

| Event | How Willow gets it (source of truth) |
|-------|--------------------------------------|
| File created / modified / deleted | Comparing folder snapshots (file hashes) each second |
| Net change per file | Line diff between the file at episode start and now |
| Write count per file | How many separate ticks touched the file this episode |
| Build/test result (pass/fail) | The real **exit code** of a command run via `willow run`, or a verified result dropped into the tray file `.willow/results.txt` |
| Turn / pause boundary | A quiet gap of ≥ 2.5s with no file changes |

**Catching the agent's own tests (Option A).** Willow watches the *folder*, so it
sees file changes but not a build/test the agent runs *inside its own session*.
To close that gap without trusting the agent's word, the project's `CLAUDE.md`
tells Claude Code to run build/test commands **through** Willow
(`willow run <command>`). Willow then captures the **real exit code** — a fact the
agent cannot fake — and announces it. If the agent ignores this, Willow simply
stays silent on pass/fail; it never invents a result. A second, general drop
point is the plain-text tray `.willow/results.txt` (for scripts/CI that already
hold a real exit code): a line like `PASS npm test` or `1 pytest` is announced as
a checkpoint.

## Episodes (chunks)

A raw stream of every write would flood the user; only the final state would hide
the trajectory. So writes are grouped into **episodes**.

**An episode ends (is announced) when either:**
1. **A checkpoint arrives** — a build/test result (`willow run`). *Or*
2. **A natural pause** — ≥ 2.5 seconds with no further file changes.

## What gets announced (the rules)

For each closed episode, Willow announces, in this order:

1. **Net file changes.** For every file touched (except reverts, see 3):
   `created X` / `deleted X` / `edited X (+A -B lines)`.
   - If a file was written **3+ times** in the episode, append `, N times`
     (this is *churn* — a fact a sighted developer reads from the file list).
2. **Multiple files** are collapsed to `N files: …; …` rather than one line each.
3. **Silent undo.** If a file was written 2+ times but ends **identical to where
   it started** (net diff zero), Willow says `quietly undid the change to X`
   instead of pretending nothing happened. A file that returns to its
   **original** (session-start) content is flagged `back to its earlier version`.
   This is the A→B→A revert case — behaviourally significant, easy to miss.
4. **Build/test outcome.** If the episode ended on a checkpoint:
   `then '<command>' passed` or `then '<command>' FAILED (exit code N)`.
5. **Stuck signal (derived fact).** If the **same file** is touched across
   **3 consecutive episodes that each ended in a failing check**, Willow says
   `the agent seems stuck on X (N failing attempts in a row)`. A passing check
   resets the count. No model is involved — it is counted from the log.

Empty episodes (a checkpoint with no file changes still reports the check; a
pause with no changes says nothing).

## Push vs Pull

- **Push:** the announcements above, spoken when something happens.
- **Pull:** press **Enter** any time to hear the current state
  (`state right now: … since we started`). This makes a missed push recoverable.

## What is deliberately NOT in the rulebook

- **No AI interpretation.** Willow never says *why* the agent did something or
  *what it is trying to achieve*. Those are guesses. (A model-based "semantic"
  grouping is kept as a **future study condition**, not the mechanism.)
- **No visual/layout description** in this version (the on-demand "guess"
  channel is future work).

## Adjustable numbers (in `willow.py`)

- `POLL_SECONDS = 1.0` — how often the folder is checked.
- `QUIET_SECONDS = 2.5` — the pause that ends an episode.
- `STUCK_THRESHOLD = 3` — failing episodes in a row before "stuck" is announced.
