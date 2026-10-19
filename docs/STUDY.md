# Willow follow-up: research plan and session protocol

This is a proposed study, not an approved ethics application. No recruitment,
participant contact, data collection, compensation or institutional commitments
have been made. The aim is to learn what helps blind developers regain an
accurate understanding of AI-assisted work, with manageable listening effort.

The design adapts StateScribe's change categories, episodic and object-centred
memory, user-request priority and repetition filtering:
[Chang et al., StateScribe, UIST 2026](https://arxiv.org/abs/2604.23749).
It does not transfer StateScribe's performance results to code or to Willow.

## Questions and conditions

1. Can developers accurately identify what changed after an interruption?
2. Do recorded provenance and inspectable evidence help calibrate trust,
   including knowing when authorship is unknown?
3. Which timing, detail, repetition and speech-routing choices best complement
   NVDA, JAWS, VoiceOver and Orca on participants' devices?
4. What prevents independent installation, recovery or continued use?

Use a formative, mixed-method study with 3–5 blind screen-reader users who have
coding or AI-tool experience. Include different experience levels if feasible.
Offer accessible recruitment and consent materials and participant-chosen
communication formats. This sample supports rich case analysis and iteration;
it cannot establish population-level effectiveness or decide paper format by
itself. Expand or run additional iterations if coverage or claims require it.

Use matched toy projects and counterbalance condition order:

- Baseline: `willow watch --no-briefing --no-provenance-labels --no-voice`.
  Existing state, diff and recap remain available. Do not use `back` in this
  condition because it deliberately supplies the experimental briefing.
- Memory: `willow watch --no-voice`, with briefing, history and evidence.
- Explore speech preference after the comparison: system speech, a supported
  Windows bridge, on-demand versus automatic speech. Let participants choose
  their working configuration; do not force speech that masks their reader.

Do not conflate memory benefits with model quality: use fixed factual wording
in the primary study. Keep optional narration and visual descriptions off.
For a later narration study, the legacy wording guard is a heuristic and needs
separate semantic-faithfulness evaluation.

## Remote session, up to 120 minutes

1. 0–10 minutes: accessible consent, recording choices, background, screen-reader
   and OS versions. Explain pause/withdrawal and compensation independently of
   task performance. Provide a way to stop that does not depend on Willow.
2. 10–30: participant installs on their own machine using SETUP.md, with only
   assistance they request. Record time, errors and intervention type. Use a
   researcher-prepared toy repository, never their private work.
3. 30–40: practice state, diff, recap, stop, briefing, history and evidence.
   Check that keyboard focus and screen-reader review remain usable.
4. 40–65: first condition. Participant edits a small project, then takes a break.
   The researcher/harness applies a fixed mutation set. For the memory condition
   use `away` before the break and `back` after it; for baseline keep timing
   equivalent and offer ordinary state/diff/recap. Do not require continuous
   think-aloud while listening. Ask for a short account of what changed and why
   they believe each claim. Record reorientation time without coaching.
5. 65–70: rest and an optional short workload rating.
6. 70–95: second condition with a matched project and different equivalent
   mutations. Counterbalance order across participants.
7. 95–110: choose speech preferences and try pause/resume/stop with the screen
   reader active. Record collisions and recovery, not only preference ratings.
8. 110–120: interview and debrief. Ask about missing facts, excessive detail,
   author uncertainty, trust, privacy and whether they would use Willow again.

Allow setup to take longer or split the visit. A participant whose setup fails
still contributes a valuable accessibility case; do not silently exclude it.

## Fixed changes and ground truth

Use the same independent ground-truth checklist in both conditions. Include a
new file, removed file, unique-content move, dependency added or removed,
version changed, JSON setting changed, human declaration, harness-recorded agent
edit, and uninstrumented edit with unknown author. Include an unchanged file
and a distracting burst of repeated edits. Preserve Git diffs and the mutation
script as the oracle. A Git author name is not an oracle for human/AI origin.

Use a researcher's recorded Edit/Write hook or `willow record --actor agent PATH`
for instrumented actions. The latter is a declaration, so score its evidence
label accordingly. Store a separate researcher action sheet to evaluate whether
provenance is justified. Do not mark an honest “unknown” as an incorrect guess.

## Measures and analysis

- Reorientation: time to an accurate account, change recall and false claims
  against the ground-truth checklist; report per participant and condition.
- Provenance: correct recorded/declared/unknown labels, incorrect assertions,
  unknown rate and evidence-inspection behavior. Report coverage separately
  from accuracy among known labels.
- Interaction: requested repeats, expansions, history/evidence use, speech
  stops, collisions, lost focus, recovery time, and researcher assistance.
- Experience: short participant-defined workload and confidence ratings,
  perceived control, usefulness and privacy concerns, followed by interview.
- Technical: precision/recall/F1, duplicate facts, detection/checkpoint latency,
  storage over 110 revisits, unreadable files, branch switches, large projects,
  non-ASCII paths, binary files and corrupt memory. `evaluate_memory.py` covers
  synthetic fixtures and small-project scaling; add held-out real task traces.

Keep task-ground-truth scoring separate from interview coding. Have a second
coder independently review a planned subset of transcripts and all ambiguous
faithfulness/provenance cases where resources allow; reconcile and document
disagreements. Choose and explain the analysis method before claiming coding
reliability; do not manufacture a reliability number from consensus coding.
Use descriptive and qualitative findings for 3–5 cases, not a claim of a
powered population comparison. Keep the developer's longitudinal diary distinct
from independent participant findings and acknowledge researcher involvement.

## Device acceptance worksheet

Record OS, CPU/Python architecture, terminal, screen reader, version, speech
backend, whether the step passed, evidence, and any assistance. All rows are
**pending real-device validation** until someone performs them.

| Configuration | Install and doctor | Keyboard/review | Stop and overlap | Reopen and away/back |
|---|---|---|---|---|
| Windows / NVDA / text | Pending | Pending | Pending | Pending |
| Windows / NVDA controller | Pending | Pending | Pending | Pending |
| Windows / JAWS / text | Pending | Pending | Pending | Pending |
| Windows / JAWS COM | Pending | Pending | Pending | Pending |
| macOS / VoiceOver / text and say | Pending | Pending | Pending | Pending |
| Linux / Orca / text and Speech Dispatcher | Pending | Pending | Pending | Pending |
| Linux / eSpeak fallback | Pending | Pending | Pending | Pending |

Accept a setup only after the user can install, inspect a known change, request
and stop speech, recover an intentionally missing backend, and reopen with an
accurate briefing. Include Windows lock-screen suppression in bridge testing.

## Data handling and decisions still needed

The application uploads no telemetry. Its local logs can contain file paths,
commands and failure text, including accidental sensitive content. SQLite
memory retains hashes and setting fingerprints, not file contents. Use only
toy projects. Obtain separate consent for screen/audio recording, local log
export, optional continued use and quotations. Screen sharing and recordings
can capture unrelated windows: let participants stop sharing and redact them.
Agree who can access data, where it is stored, retention, deletion/withdrawal
procedures and accessible participant information before recruitment. A local
recording is not automatically consented research data.

Mark and the institutional team need to confirm PI sponsorship and ethics
eligibility, the approved recruitment/payment route and budget, study materials,
and whether a second coder is available. The proposed £250 would allow £50 for
each of five participants; review the rate and treatment of setup/withdrawal
with the institution. These are open decisions, not commitments by Willow.

## Working milestones from the correspondence

- By 20 November 2026: prepare ethics application, accessibility pilot and
  participant materials, subject to institutional sponsorship.
- By mid-December 2026: complete real-device acceptance, fix installation and
  speech issues, freeze the study build and independent mutation oracle.
- January–February 2027: sessions only after approval; iterate if early cases
  expose barriers, recording changes to the protocol and build.
- By 19 March 2027: draft with technical results, case analysis, limitations and
  reproducibility materials.
- April 2027: publication target from the correspondence. Verify the actual
  ASSETS 2027 dates and track requirements before scheduling a submission.

Full versus short paper should follow the contribution and evidence gathered,
not be assumed from the proposed sample size or an unverified deadline.
