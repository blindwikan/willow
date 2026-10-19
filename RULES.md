# Willow's Rulebook (the "state machine")

This document is the fixed set of rules Willow uses to decide **what you hear**.
It exists because *grouping is not just presentation* — deciding what reaches the
user carries real inferential weight. So the decision must be a **deterministic
rule over event types already in the log**, not an AI prompt. The same events
always produce the same announcement. It is reviewable, and it behaves the same
across model versions.

This is a deliberate design decision, not an incidental one.

**Every rule on this page has a test in `test_willow.py` that pins the exact
sentence it produces.** That is not decoration: an earlier version of this page
described behaviour the code did not implement, and nothing caught it. The rules
below and the code are now checked against each other on every run.

## The event types Willow knows about

All are **facts** built from *outside* the agent, so they are verifiable:

| Event | How Willow gets it (source of truth) |
|-------|--------------------------------------|
| File created / modified / deleted | Comparing folder snapshots (size, timestamp, then content hash) |
| Net change per file | Line diff between the file at chunk start and now |
| Rename | A delete and a create in one chunk with **identical content hashes** |
| Repeated writing | Ticks that touched the file this chunk — or, with a write hook, one event per real write |
| Build/test result (pass/fail) | The real **exit code** of a command run via `willow run`, a Claude Code hook, or a verified result dropped into `.willow/results.txt` |
| Why it failed | Text **quoted verbatim** from the command's own output |
| Notices | Things the harness reports that carry no verdict (e.g. Claude waiting for permission) |
| Turn / pause boundary | A quiet gap of ≥ 2.5 s with no file changes |

**Catching the agent's own tests.** Willow watches the *folder*, so it sees file
changes but not a build or test the agent runs inside its own session. Two ways
to close that gap, neither of which trusts the agent's word:

1. **`CLAUDE.md`** tells the agent to run build/test commands through Willow.
   `willow init` writes this rule with the correct path already filled in.
2. **Claude Code hooks**, also installed by `willow init`. A hook is executed by
   the *harness*, not by the model, so what it reports is external fact in
   exactly the way an exit code is. This works even when the agent forgets the
   rule.

If neither fires, Willow simply stays silent on pass/fail. It never invents one.

**Where an unknown must stay unknown.** If a hook payload carries no unambiguous
exit code, Willow announces only that a command *ran* — *"the agent ran 'npm
test' (I couldn't check whether it passed)"*. An unknown is never converted into
a verdict. Likewise, a command that could not be **launched** at all (a typo, a
missing tool) is reported as *"I couldn't run it"* — it is not a test failure,
and Willow will not describe it as one.

## Episodes (chunks)

A raw stream of every write would flood the user; only the final state would hide
the trajectory. So writes are grouped into **episodes**.

**An episode ends (is announced) when either:**
1. **A checkpoint arrives** — one or more build/test results. *Or*
2. **A natural pause** — ≥ 2.5 seconds with no further file changes.

If several results arrive together, **all of them are announced, in order**. A
failure is never dropped because a pass arrived alongside it.

## What gets announced (the rules)

For each closed episode, Willow announces, in this order:

1. **Renames.** A delete and a create sharing a content hash: `renamed X to Y`.
2. **Net file changes**, for every other file touched:
   `created X (N lines)` / `deleted X` / `edited X (+A -B lines)`.
   - `deleted X, which it created earlier` when the file did not exist at the
     start of the session. It is a deletion, not a return to a version that
     never existed.

   - **Repeated editing** is appended as `, changed repeatedly` or
     `, written N times` — see the section below for which, and why.
3. **Multiple files** are collapsed into `N files: …; …` when spoken, but printed
   **one per line** so a screen reader can be arrowed through them.
4. **Silent undo.** If a file was written 2+ times but ends **identical to where
   it started**, Willow says `quietly undid the change to X` rather than
   pretending nothing happened. A file that returns to its **session-start**
   content is reported as `put X back to the version it had when we started`.
   This is the A→B→A case — behaviourally significant, easy to miss.
5. **Notices**, relayed as written, never as a verdict.
6. **Build/test outcome.** `then '<command>' passed in N seconds`, or
   `then '<command>' FAILED (exit code N)` followed, when the output allows it,
   by a short **verbatim** quote of the runner's own summary and first failure.
   Quoting output is not interpretation — it is evidence of the same kind as the
   exit code, and it is the difference between a notification and an answer.
7. **Stuck signal (derived fact).** Counted per **(command, file)** pair: when
   the same file is touched in `STUCK_THRESHOLD` episodes that each ended with
   the same command failing, Willow says
   `the agent has changed X in N attempts that each ended with '<command>'
   failing`. A pass of that command resets **every** streak for it.
   The wording states only what was observed. Willow does not claim the agent is
   stuck *on* that file — it cannot know that; it can only count.

Empty episodes: a checkpoint with no file changes still reports the check; a
pause with no changes says nothing.

## Repeated editing of the same file

How this is reported depends on how well Willow can actually count, and the
wording says which:

| What Willow has | What it says |
|---|---|
| Polling only, file differed on **3+ ticks** | `, changed repeatedly` |
| A **write hook**, **2+ writes** | `, written N times` |

The distinction is the facts-vs-guesses rule applied to a number. Polling can
only count the ticks on which a file *happened to differ*, which is not the
number of edits: five rewrites inside one 0.7 s tick look like one, and one slow
write spread across two ticks looks like two. So on polling alone Willow says
`changed repeatedly` and **no number at all** — a count it cannot stand behind
must not be spoken as though it could.

A Claude Code `PostToolUse` hook on `Edit`/`Write`/`MultiEdit`/`NotebookEdit`
reports **one event per actual write**, from the harness rather than from the
model. That is a real count, so it is said as one. `willow init` installs this
hook.

The hook also catches a case polling can never see: a file **changed and changed
straight back inside a single tick** never differs at any moment Willow looks at
it, so watching alone finds nothing to report at all. With write events, Willow
knows two writes happened and the content is unchanged, and announces the silent
undo. A single write that left the bytes identical is not reported — nothing
happened.

## Push vs Pull

- **Push:** the announcements above, spoken when something happens.
- **Pull:** ask any time — Enter for the current state, `diff` to hear what
  actually changed, `recap` for the session so far. This makes a missed push
  recoverable, so all of it is answered from what the watcher already knows and
  returns immediately.

**Priority and interruption.** Announcements are not strictly first-in-first-out.
Failures, reverts, stuck signals and anything you asked for are **urgent** and
jump ahead of routine file listings; a backlog of routine announcements is
trimmed rather than queued indefinitely; and `stop` clears the queue and cuts off
the current sentence. Being three announcements behind, with no way to skip, is
worse than missing one.

## Optional natural narration (a wording layer, not a decision layer)

Run `watch --narrate` and Willow will say the **same announcement in more
natural language**. This is a **wording layer only**, and it changes nothing in
the rulebook above:

- The rules on this page still decide **what** is announced. A model is given the
  finished factual line (e.g. `edited app.py (+4 -1 lines). then 'pytest'
  passed.`) and asked only to **re-word** it. It never sees the code or the
  agent's own account.
- **The result is then checked against the facts before it is spoken.** Every
  number, filename and pass/fail token in the narration must already appear in
  the verified facts; anything else is discarded and the plain wording is used.
  See `faithful()` in `willow.py`.

  This matters more than it might seem. "Never say a build passed unless the
  facts say so" is an instruction, and an instruction is enforced by nothing —
  least of all on a three-billion-parameter model running locally, or on whatever
  is behind a user-supplied `base_url`. Checking the output turns the central
  safety claim from a hope about a prompt into an invariant with a test, and it
  is what makes running a small local model a defensible choice rather than a
  risk.
- Making the sentence *speakable* — "plus 12, minus 3 lines" — is a separate
  layer just above the voice, not the model's job. That is what lets the check
  above stay strict about filenames.
- The **exact factual line is always written to the log** as ground truth; the
  natural version is logged beside it on a `(spoken)` line.
- If narration is unavailable, unfaithful, or **slower than 1.5 seconds**, Willow
  falls back to the plain factual wording. It never blocks the watching and never
  guesses. Narration happens on the speaking thread, so a slow model cannot shift
  where an episode boundary falls.

Any backend may be used — Anthropic, OpenAI, a local model through Ollama, or
anything OpenAI-compatible. The rulebook and the faithfulness check are identical
whichever it is, which is what makes the comparison between them meaningful.

The suggested local model is **`gemma4`**, because it takes images as well as
text and so covers both the narration and `describe` from a single pull. Willow
prefers the smallest installed variant for narration: rewording a one-line fact
inside a 1.5 second budget does not reward a larger model, it just misses the
deadline and falls back to the plain wording. No model is ever forced — the
preference only chooses among what is already installed, and `text_model` /
`vision_model` in the config override it outright.

## Optional visual description (`describe`) — the on-demand "guess" channel

Type `describe` while watching and Willow describes how the app **looks**. A
screen description is unavoidably an interpretation, so this is kept firmly
**separate from the facts**:

- Willow **finds the app's browser window itself**, preferring one whose title
  matches a detected local dev server port.
- It announces the **window it captured** — the window's real title is a
  **fact** — and only then speaks the description, **explicitly labelled an
  interpretation**. Because the user can't see the screen to check, that spoken
  window name is the safety net: a wrong grab is heard immediately, and `other`
  moves to the next candidate.
- If the window can't be found, is minimised, or no configured model can look at
  pictures, Willow **says exactly that**, including what to do about it. It never
  invents a description.

This is the **guess** side of the facts-vs-guesses split made explicit: the
window identity is a checkable fact; the look is an owned-up-to guess.

## What is deliberately NOT in the rulebook

- **No AI interpretation of intent.** Willow never says *why* the agent did
  something or *what it is trying to achieve*. Those are guesses. The optional
  narrator only re-words established facts. (A model-based "semantic" *grouping*
  is kept as a **future study condition**, not the mechanism.)
- **No *automatic* visual description.** `describe` is on-demand only.
- **No changing your files.** Willow reports a silent revert; it does not offer
  to undo it. A tool that speaks and a tool that writes are different trust
  levels, and this one only speaks.

## Adjustable numbers

In `.willow/config.json` (see the README), not in the source:

- `poll_seconds = 0.7` — how often the folder is checked.
- `quiet_seconds = 2.5` — the pause that ends an episode.
- `stuck_threshold = 3` — failing episodes before "stuck" is announced.
- `narrate_timeout = 1.5` — the hard budget for a natural rewording.
- `voice_rate = 3` — speaking rate, -10 to 10.

## Revisit memory and provenance (2.1)

The examples earlier on this page show the legacy file wording. By default,
2.1 appends an authorship label to each live file phrase: `[author unknown]`,
`[agent recorded]`, `[user recorded]`, or `[mixed provenance]`. Set
`provenance_labels` to false only for the explicit legacy study condition.
New memory descriptions always use fixed wording, even with narration enabled.
The default stuck summary says `PATH changed in N recorded attempts that each
ended with 'COMMAND' failing`; it does not assign unknown edits to the agent.

| Recorded condition | Wording / behavior |
|---|---|
| No previous session | `This is the first recorded session. I saved a baseline for your next visit.` |
| Reopen without changes | `Since your last checkpoint, DATE: no verified net file changes.` |
| Unclean preceding exit | Append `The previous session ended without a final checkpoint.` |
| Unreadable path | Keep its last known state; append `Some paths could not be read; I have not treated them as deleted.` |
| File appears / disappears / changes | `PATH appeared`, `PATH removed`, or `PATH modified`, followed by authorship |
| Unique identical-content move | `OLD moved to NEW (identical content; inferred move)`, followed by authorship |
| Ambiguous identical content | Separate additions/removals; no move verdict |
| Structured setting change | `setting FIELD modified in PATH`, followed by authorship |
| Package appears / disappears | `package FIELD appeared in PATH` or `package FIELD removed in PATH`, followed by authorship |
| Matching recorded agent content | `agent (recorded)`; source is inspectable |
| Matching human declaration | `you (recorded)`; evidence says it is a declaration |
| Missing or stale provenance | `author unknown` |
| Conflicting matching provenance | `mixed provenance` |
| Evidence | Append `Evidence NUMBER.`; `evidence NUMBER` prints the source and endpoint hashes |

Recent snapshots and per-file history persist in `.willow/memory.sqlite3`.
Session/fact retention is bounded independently. A return briefing reports net
changes after the preceding checkpoint, not every intermediate event. Corrupt
memory produces an explicit unverifiable-briefing message, never an empty
baseline pretending no changes occurred. A standalone `willow briefing` does
not advance memory or assign new evidence IDs.

`away` saves an interruption baseline and selects on-demand speech; `back`
compares against it and restores the preceding speech mode. A second `away`
does not reset the baseline. An aggregate break fact reuses recorded authorship
only when its before and after hashes match a retained fact after that baseline.

## Speech policy (2.1)

- Default `speech_mode` is `on-demand`. Startup instructions and the return
  briefing are spoken; automatic change episodes are text only. Requests are
  spoken unless voice is disabled. `--speech auto` or `resume` enables speech
  at episode boundaries; `pause` returns to on-demand mode.
- A tick that observes a new write cannot also establish a natural quiet
  period. A subsequent quiet tick or an actual command result ends the episode.
- Within an automatic episode, failures precede routine file changes. Existing
  priority queues retain urgent items. A typed request clears the old speech
  backlog before it is answered.
- At most `briefing_limit` details (default 5) are spoken in one summary. All
  details are printed. Briefings offer `briefing all` for expansion.
- Identical routine automatic speech is filtered within `repeat_seconds`
  (default 15). Explicit requests and distinct checkpoints with urgent results
  are not repetition-filtered. Quiet mode suppresses routine automatic speech.
- `--no-voice` uses text throughout, including startup and doctor. Unsupported
  Windows screen-reader bridges report their system-speech fallback explicitly.
- macOS uses `say`. Linux tries `spd-say`, then `espeak` if missing or unsuccessful.
  NVDA uses a user-provided controller DLL; JAWS uses its installed active COM
  interface. These bridges do not establish universal screen-reader idle timing.

`test_memory.py` covers new categories, recovery, evidence, provenance,
break/reopen behavior and speech policy. `evaluate_memory.py` compares outputs
to an independently specified synthetic oracle; it does not establish usability.

## OpenAI and Codex

Direct OpenAI calls use Responses by default. Compatible servers and the
explicit `openai_api: "chat-completions"` option retain Chat Completions.
Refused, incomplete, failed or malformed Responses output is not narrated.
Memory facts and provenance are never rewritten by a model.

Codex setup preserves AGENTS.md and merges project-local hooks. Codex requires
the user to review/trust hooks in its own interface. Willow never approves
tools or requests continuation. A matched before/after patch observation can
record agent activity; missing/stale observations and uninstrumented shell
edits retain unknown authorship. `Stop` says `Codex has finished its turn`;
`PermissionRequest` says `Codex is requesting your permission`. Neither implies
a test verdict. Only an explicit integer exit code or `willow run` establishes
pass/fail. These cases are pinned in `test_openai.py`.
