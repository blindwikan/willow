# Follow-up implementation and validation

## Traceability to the correspondence and StateScribe

| Proposed point | Implementation | Verification |
|---|---|---|
| Other people's devices | One-file standard-library Python; native Windows/macOS/Linux speech; optional NVDA and JAWS bridges; text-only path; accessible setup | Backend simulations, setup regressions, cross-platform CI configuration; device acceptance pending |
| Since you were last here | Persistent checkpoints, startup briefing, explicit `away`/`back`, short and full briefings | Restart/offline-edit/break tests; synthetic oracle |
| Three change types | Object presence including manifest packages; spatial inferred moves; file and structured setting/dependency attributes | Category, ambiguity, dependency and settings tests |
| AI/fact balance | Deterministic new memory descriptions with fact IDs and inspectable hashes/source | Evidence round-trip tests; no model used for new briefings |
| User/agent distinction | Content-matched hook evidence, explicit declarations, unknown and mixed labels | Matching/stale/conflicting evidence and live/history consistency tests |
| Two-layer memory | Bounded recent session snapshots and a separately bounded, indexed file fact ledger | Session/fact retention and rename-history tests |
| Ordering/filtering | Removed objects/settings/moves before routine facts; failures before edits; routine speech repetition filtering; bounded summaries | Priority, repeated/requested speech and summary tests |
| Screen-reader overload | Default on-demand speech, pause/resume/stop, request clears routine backlog, focus buffers automatic terminal text and speech | Speech/focus-policy tests; real collision measurements still needed |
| Controlled detail | Keyboard next/previous/repeat/expand on frozen review, file history, return briefing and retained facts | Position, boundary, evidence, snapshot and empty-list regressions |
| Test freshness | Before/after observed file/Git signatures, retained results, current-state comparison independent of exit code | Real wrapped commands, later edits, changed endpoints, missing observations and restart regressions |
| Personal continuity | Working/blockers/next user notes returned at startup, labelled unverified and kept out of model/fact/study text | Persistence, privacy, control-character and corrupt-file tests |
| Opt-in measurements | Separate local ledger of action/count metrics, phase markers, reported collisions, relative-time redacted JSON | Default-off, stop/restart, schema validation, privacy and phase-duration tests |
| Faithful technical evaluation | Independent explicit fixtures, P/R/F1 and repetition; 110 revisits, timing and storage | `evaluate_memory.py`, report includes host and source hash |
| Remote formative study | Counterbalanced toy tasks, accessible setup, break/return, interview, acceptance matrix and ethics decisions | Protocol in STUDY.md; not yet conducted |

StateScribe's Sections 3.2, 4.1, 4.3–4.5 and 5 motivate these choices. Its
scene interpretation, 3D positioning and VLM event inference are not required
to compare known file bytes. Willow uses deterministic comparisons and keeps
uncertainty explicit. [Paper](https://arxiv.org/abs/2604.23749).

## Evidence and recovery

Each checkpoint writes session state and fact rows in one SQLite transaction.
There is no connection held open between operations. An interrupted session
is identified at the next visit; corrupt/newer-schema memory is preserved and
reported unavailable rather than reset as an empty project. Temporarily
unreadable paths retain their prior hashes and cannot become deletion claims.
Symlinks are skipped to avoid following content outside the watched tree.

The session snapshot stores path/hash pairs and JSON-setting fingerprints,
Python project version/runtime/dependency fingerprints, and requirements
fingerprints. Unsupported formats still produce file-level facts. Credential
filenames are excluded; arbitrary secret files cannot all be detected. Setting
values are not retained. Existing diff functionality still holds source text
in RAM and prints it when requested. Logs can include command failure text.
The separate result table retains up to 20 command lines, exit codes, durations
and endpoint observations; command arguments may themselves contain sensitive
text. Failure snippets are excluded from that table. Notes are separate plain
text in `handover.json`. Study metrics use a separate `study.sqlite3` ledger
with an allowlist; exports do not include source, paths, hashes, commands or
note text. The study ledger is created only on explicit opt-in and is not
automatically pruned; stop recording and apply the approved data-retention plan.

Git HEAD and branch identify context. They are not used to infer human/AI
authorship, and Git is optional. A branch-context change is announced in a
return briefing. Content identity suggests a move; it does not prove a rename
operation. A move combined with edits is currently reported as removal/addition.
Unobserved edits that return to identical bytes cannot be recovered from
snapshots. Changes already checkpointed during an earlier watch are available
through file history; the startup briefing covers the gap after its last
checkpoint. Use `away`/`back` to mark an interruption while keeping watch open.

Provenance records bind an actor/source to an observed post-write hash. This
does not prove exclusive authorship or cover arbitrary shell-based agent edits.
Explicit `record` commands are declarations, labelled in the evidence. Missing
or stale evidence yields unknown. Conflicting matching declarations yield mixed.
Hashes describe file content, not intent, correctness or accessibility impact.

Default limits are 30 sessions and 10,000 facts. Older history is logically
pruned; this is not secure erasure, and SQLite can retain allocated pages. Logs
and inbox files have a separate lifecycle and should be reviewed before export.
The first 500 JSON fields at up to five nested levels are fingerprinted.
Large, binary, malformed, or unsupported settings retain file-level coverage.

## Scope of validation

Run checks from the repository:

```sh
python willow.py run python -m unittest discover
python willow.py run python evaluate_memory.py --output .willow/evaluation.json
```

The regression suite preserves original rulebook wording under the explicit
legacy label setting and tests the new default behavior separately. CI covers
Windows, macOS and Linux on Python 3.10–3.15, plus Python 3.8/3.9 on Linux. Adding
the workflow does not mean its remote jobs have run. Local results apply only
to the executing host. Synthetic precision is not a user-study result.

Native NVDA/JAWS audio, VoiceOver/Orca interaction, installer independence and
speech-collision behavior require device validation. No universal screen-reader
speech-idle detection is implemented. Native `say` and Linux engines are
separate speech channels; text-only and on-demand modes are the common fallback.
Focus additionally prevents automatic watcher terminal text from interrupting
screen-reader review; explicit requests are still immediate. Its bounded,
in-memory review buffer is not a replacement for persistent file history.
The optional legacy Windows asynchronous voice host may queue speech internally;
it is disabled by default so the Python queue controls the system-speech backlog.
`stop` cancels speech, but exact hardware timing requires measurement.

Build/test freshness observes the same bounded file scope and Git context as
the watcher. Equal endpoints establish only matching observations: external or
ignored dependencies, actual test coverage and transient reverted edits remain
unknown. Differing endpoints explicitly make the tested version uncertain.

Optional automatic screenshot capture remains Windows-only. The existing
legacy narration guard checks numbers, paths and verdict vocabulary; it does
not prove semantic faithfulness. New evidence-bearing changes bypass it. Do
not include optional narration in a strict-factual study condition without a
separate evaluation.

## Next research decisions

Use participant feedback to tune the five-fact default, category priorities,
repeat interval, preferred speech route and the amount of provenance detail.
Add real project traces and edited-move cases to a held-out technical corpus.
Prioritise reproducible installation or speech failures found on actual devices
before expanding optional descriptions or natural-language querying.

## OpenAI and current Python compatibility

Direct OpenAI calls use Responses; an explicit legacy mode and compatible
servers retain Chat Completions. Codex setup writes AGENTS.md and merges hooks
for patch observations, tool results, permission requests and turn completion.
See OPENAI.md for the supported interfaces and trust review required in Codex.

CI resolves current patch versions and upgrades pip, setuptools, wheel and
build to their newest compatible releases for each interpreter. It runs the
suite and synthetic evaluation, builds wheel/source distributions, installs the
package and checks its installed entry point. Python 3.15.0 is a stable release:
[Python.org release notes](https://www.python.org/downloads/release/python-3150/).
Historical Python versions get the newest tools compatible with their version;
they cannot necessarily install the same setuptools release as Python 3.15.
No OpenAI SDK dependency has been added to the distributed package.
