# Willow

**Willow is a companion narrator for blind builders working with AI coding
agents.**

While an AI agent edits your project, Willow watches the folder and tells you —
**out loud and in text** — what the agent actually did: which files changed,
what changed in them, whether builds and tests really passed or failed, and
whether the agent quietly undid its own work.

Every update is a **fact you could check yourself**. Willow never guesses. If it
cannot verify something, it says so, or says nothing — it never fills the gap.

Version 2.1 adds persistent project memory, return briefings, per-file history,
recorded authorship, speech on demand, focus mode, a keyboard change browser,
observed test-version freshness, handover notes, and opt-in study metrics.
The implementation is inspired by
[StateScribe](https://arxiv.org/abs/2604.23749), adapted to code rather than scenes.
See the [accessible setup guide](docs/SETUP.md), [study protocol](docs/STUDY.md),
and [implementation and validation notes](docs/IMPLEMENTATION.md).

**Installing this checkout:** run `python -m pip install .` from this folder.
The package index may have an earlier version until 2.1 is released.

---

## Setting up (about a minute)

You need **Python 3.8 or newer**. Nothing else: Willow uses only what comes with
Python and your computer's built-in voice.

```bash
pip install willow-narrator
```

Then, in your project folder:

```bash
willow init
```

That one command does everything that used to be manual: it writes the rule that
tells your AI agent to run tests through Willow (with the correct path already
filled in), adds `.willow/` to your `.gitignore`, and installs project-local
Claude Code and Codex hooks. Use `--agent codex` or `--agent claude` to choose
one integration; the default is `both`. Existing instructions are preserved.
Codex reads `AGENTS.md`; Claude Code reads `CLAUDE.md`. Review and trust Codex's
new hook definitions using `/hooks` before relying on hook announcements.

The hooks are worth having. They come from the harness rather than from the
model, so they work even when the agent forgets the rule — and they let Willow
tell you **when Claude is waiting for your permission**, and **exactly how many
times a file was written** rather than only how often polling happened to notice.

If you'd rather not install anything, download `willow.py` and use
`python willow.py` everywhere this page says `willow`. Everything works the same.

Any time something seems wrong:

```bash
willow doctor
```

`doctor` checks Python, your voice, your model setup, whether a Willow is
actually watching, and whether your project is set up — and **says the answer out
loud**. Every failure in a tool like this looks like silence, so being able to
ask "are you working?" and hear a real answer matters.

---

## Using it (two windows)

### Window 1 — Willow watches

```bash
willow watch
```

Willow says *"starting up"*, then *"watching … I'll record what changes."*
**Wait for that second line** — anything that changes before it counts
as how the project already was. Leave this window open.

Speech is **on demand by default**, apart from the startup instructions and
return briefing. Automatic changes remain readable as text. Use
`willow watch --speech auto` to hear changes at episode boundaries. `pause`
returns to on-demand speech; `resume` enables automatic speech. This does not
detect when VoiceOver, Orca, NVDA or JAWS is reading; you control the timing.

Use `willow watch --focus` to buffer automatic **text and speech** while you
work. Type `review` when ready, then `next`, `previous`, `repeat` or `expand`.
Explicit requests still answer immediately. `focus off` allows new updates
without dumping the backlog. Focus never hides changes from project memory.

### Window 2 — you and your AI agent

Work with your agent as normal. When you want to **build or test**, run it
through Willow so the real result is captured:

```bash
willow run npm test
```

Willow runs the command, shows its output as usual, and the watching window
announces the true outcome — *"then 'npm test' FAILED, exit code 1. 3 failed,
12 passed. First failure: tests/test_auth.py::test_login_redirect."*

With `willow init` done, your agent runs its own tests through Willow too, so
you hear real results rather than the agent's description of them. If the agent
forgets, Willow simply stays quiet about pass/fail — it never invents one.

---

## What you can ask, while watching

Type any of these and press Enter:

| Type this | What happens |
|---|---|
| *(just Enter)* | The state right now — "3 new files, 1 changed since we started" |
| `diff` | Reads out **what** actually changed in the last chunk, line by line |
| `recap` | A summary of the whole session |
| `briefing` / `briefing all` | Changes since your preceding visit, with evidence numbers |
| `away` / `back` | Save a break baseline, then hear what changed while away |
| `history PATH` | Retained history for a file, including inferred moves |
| `browse` | Open the latest 100 retained change facts, one at a time |
| `next` / `previous` / `repeat` / `expand` | Navigate the active list or inspect its details and evidence |
| `focus on` / `focus off` / `focus status` | Buffer automatic text and speech, resume new output, or check the buffer |
| `review` / `review clear` | Browse buffered updates or clear their buffer; facts and logs remain |
| `checks` / `checks all` | Latest result or last 20, with observed project-version freshness |
| `note working TEXT` / `note blockers TEXT` / `note next TEXT` | Save personal handover notes, labelled as unverified user notes |
| `note` / `note clear [FIELD]` | Read notes or clear one/all fields |
| `study start CODE` / `study stop` / `study status` | Explicitly start, stop or check local study metrics |
| `study mark PHASE` / `study collision` / `study export [PATH]` | Mark a phase, report speech overlap, or export redacted metrics |
| `evidence NUMBER` | The recorded source, category, authorship and endpoint hashes |
| `pause` / `resume` | On-demand / automatic speech at natural pauses |
| `describe` | What your app **looks** like right now |
| `other` | Describe a different window — if `describe` grabbed the wrong one |
| `stop` | Stop talking right now, and drop the backlog (also `shh`) |
| `faster` / `slower` | Change the speaking rate |
| `quiet` / `loud` | Speak only the important things, or everything |
| `help` | List these |
| `q` | Quit |

`stop`, `faster` and `slower` matter more than they look. Willow's default rate
is set for a screen-reader user, not a first-time listener — and being three
announcements behind with no way to skip is worse than missing one.

`willow run` observes hashes and Git context before and after the command.
`checks` tells you if later edits differ, or if the command changed files and
the tested version is uncertain. Matching endpoints do not establish coverage
or detect temporary edits that were reverted during a command.

You can manage notes without a watcher using `willow handover --next "Review
the failing test" --folder PATH`. Notes return at startup, stay out of factual
history and announcement logs, and are never sent for model narration.

Study recording stays off until `study start P01`. The separate local ledger
accepts counts, canonical action names, relative timings and restricted phase
codes. Its export excludes source, paths, command arguments, notes and
announcement text. Recording continues across watcher restarts until `study
stop`; active recording is announced at startup. Use participant codes and
approved consent procedures. See the [study recording protocol](docs/STUDY.md).

---

## What Willow will say — examples

- *"created login.js (14 lines)."*
- *"2 files: edited login.js (+12 -3 lines); edited styles.css (+4 lines).
  then 'npm test' passed in 4.2 seconds."*
- *"edited app.py (+1 -1 lines), written 5 times."*  ← repeated editing
- *"quietly undid the change to app.js."*  ← a silent undo you'd normally miss
- *"renamed old-name.js to new-name.js."*
- *"then 'pytest' FAILED (exit code 1). 3 failed, 12 passed. First failure:
  tests/test_auth.py::test_login_redirect."*
- *"the agent has changed login.js in 3 attempts that each ended with 'npm test'
  failing."*
- *"Claude is waiting for permission to run npm install."*
- *"state right now: 3 new files, 1 changed file since we started."*

When several files change at once, the spoken version is collapsed into one
sentence, but the text prints **one file per line** so you can arrow through it.

New announcements label each file as `author unknown`, `agent recorded`,
`user recorded`, or `mixed provenance`. A matching Claude write hook records
agent activity; other agents and people can declare a change using
`willow record --actor user PATH` or `willow record --actor agent PATH`.
Declarations are labelled as such in the evidence. Git author names are never
treated as proof that a human or AI made a change. A matching hook also does
not prove exclusive authorship when editing concurrently.

Reopening `watch` compares against the last saved checkpoint. The short briefing
speaks at most five facts and offers the rest on request. Removed files and
packages come first, followed by settings and moves. Each fact has an evidence
number. Exact-content move matches are explicitly **inferred**, since deleting
a file and creating an identical copy is also possible. Ambiguous matches are
reported as separate additions/removals.

The memory stores recent session snapshots and a longer per-file fact history
in `.willow/memory.sqlite3`. It stores hashes and setting fingerprints, not source
text or setting values. JSON settings, npm manifests, Python project metadata
and requirements files have structured change tracking; other formats have
file-level tracking. Private credential filenames are excluded. Unknown formats,
unreadable paths and unobserved changes remain uncertain.

Outside the watching window, `willow briefing`, `willow history PATH`, and
`willow evidence NUMBER` print readable facts. Add `--folder DIR` for another
project and `--json` for structured output. The standalone briefing compares now
against the last checkpoint without advancing it; it does not create evidence
numbers. Inside the watcher, `briefing` repeats its saved return briefing.

---

## Optional: a more natural voice

By default Willow words its announcements with a fixed rulebook — no internet,
no account, no model involved. If you'd like the **same facts** said more
naturally:

```bash
willow watch --narrate
```

The model is given Willow's finished factual line and asked only to re-word it.
It never sees your code or the agent's account of its work.

**And Willow checks the answer before speaking it.** Every number, filename and
pass/fail in the spoken sentence must already appear in the verified facts;
anything else is thrown away and the plain wording is used instead. So the
promise that narration cannot invent a test result doesn't rest on the model
behaving — it's enforced, and it holds for small local models too.

Those legacy checks are syntactic safeguards, not a proof of arbitrary prose's
meaning. The new briefings, history, evidence and authorship-labelled changes
always use deterministic wording; they are never rewritten by a model.

The exact facts always go to the log as the citable ground truth, with the
spoken version beside them.

---

## Optional: run the model on your own machine

Willow works with **Anthropic**, **OpenAI**, or a **local model through Ollama** —
and with anything else that speaks the OpenAI API (LM Studio, vLLM, llama.cpp,
OpenRouter…).

Direct OpenAI requests now use the **Responses API** for text and images.
Set `OPENAI_API_KEY` or the private `~/.willow/openai_key` file, then run
`willow watch --narrate --provider openai`. Choose models with `--model NAME`
and `--vision-model NAME`. To keep a legacy deployment, use
`--openai-api chat-completions`. Compatible local servers keep their existing
Chat Completions protocol. No OpenAI Python SDK is required. See the
[OpenAI and Codex guide](docs/OPENAI.md) for setup and evidence limitations.

**A local model is the recommended choice.** Narration sends your file paths
somewhere, and `describe` sends a picture of your screen. With Ollama, neither
leaves your computer — and there is no key and no cost.

```bash
# once - install Ollama from ollama.com, then:
ollama pull gemma4

# then
willow watch --narrate --provider ollama
```

**`gemma4` is the suggested model**, and one pull is all you need: every Gemma 4
variant takes images as well as text, so the same model does the narration *and*
`describe`. Ollama keeps one model in memory at a time, so this also means it
never has to unload several gigabytes and reload something else when you ask
what your app looks like.

Sizes, if you want to choose: `gemma4:e2b` (~7 GB) is the lightest,
`gemma4` / `gemma4:e4b` (~10 GB) is the default, and `gemma4:12b`, `gemma4:26b`
and `gemma4:31b` go up from there. For narration, smaller is genuinely better —
it's a rewording job on a 1.5 second budget, and a big model just misses the
deadline and gets replaced by the plain wording.

Willow finds Ollama by itself and picks the best model from whatever you happen
to have installed, preferring a small Gemma 4 and falling back to Llama, Qwen,
Phi or Mistral. It warms the model up at startup so the first announcement isn't
slow. If nothing installed can look at pictures, `describe` says exactly that and
tells you what to pull — it never makes something up. Set `text_model` or
`vision_model` in the config to override any of this.

**Choosing a provider**

```bash
willow watch --narrate --provider anthropic     # needs ANTHROPIC_API_KEY
willow watch --narrate --provider openai        # needs OPENAI_API_KEY
willow watch --narrate --provider ollama        # needs nothing
willow watch --narrate --provider openai-compat --base-url http://localhost:1234/v1
```

With no `--provider`, Willow uses a key you already have, and otherwise falls
back to a local Ollama if one is running. Keys are read from the environment or
from `~/.willow/anthropic_key` / `~/.willow/openai_key` — never from inside your
project, so they can't end up in your repository.

Narration is held to a **1.5 second budget**. If the model is slower than that,
Willow speaks its own plain wording instead. A more natural sentence that
arrives four seconds late is worse than a plain one that arrives now.

---

## Optional: hear what your app *looks* like

While Willow is watching, type **`describe`** and press Enter.

- Willow **finds your app's browser window itself** — you don't have to aim it.
- It **names the window it grabbed first** (a fact — e.g. *"Chrome — 'Welcome —
  Sign in'"*), then describes how it looks, clearly labelled as an
  **interpretation, not a checked fact**. If it ever grabbed the wrong window
  you'd hear the wrong name and know to ignore the rest — type `other` to move
  to the next one.
- If it can't find your app, or the window is minimised, or no model can see
  pictures, it **says so plainly**.

Have your app open in a browser first. **Windows only for now.**

---

## Settings

Everything adjustable lives in `.willow/config.json` in your project (and
`~/.willow/config.json` for all projects). `willow init` writes a starter one.

```json
{
  "voice_rate": 3,
  "verbosity": "normal",
  "provider": "ollama",
  "text_model": null,
  "vision_model": null,
  "poll_seconds": 0.7
}
```

Additional defaults are `speech_mode: "on-demand"`, `speech_backend: "auto"`,
`briefing_limit: 5`, `repeat_seconds: 15`, `memory_sessions: 30`, and
`memory_facts: 10000`. `nvda_dll` optionally selects a user-supplied NVDA client
DLL. Windows also supports `--backend jaws`; macOS uses `say`, and Linux uses
Speech Dispatcher or eSpeak. See [platform setup](docs/SETUP.md) for details.
`--no-memory` disables the session ledger without deleting existing records.
Retention limits remove old records logically; they are not a secure-erasure
mechanism. Logs and inbox files have separate retention: `willow.log` rotates,
while JSONL events and inbox files grow until you remove them with Willow stopped.

`voice_rate` runs from -10 to 10. Command-line flags beat environment variables
(`WILLOW_PROVIDER`, `WILLOW_BASE_URL`,
`WILLOW_TEXT_MODEL`, `WILLOW_VISION_MODEL`, `WILLOW_VOICE_RATE`), which beat
project config, user config, and defaults, in that order.

---

## Good to know

- Willow keeps a small folder called `.willow` inside your project: a readable
  log at `.willow/willow.log`, and a structured one at `.willow/events.jsonl`.
  It **never changes your files** — it only looks at them.
- It ignores noise like `node_modules`, `.git` and build output, and in a git
  repository it also skips whatever your `.gitignore` already excludes.
- It works on **Windows and Mac** with the built-in voice. On **Linux** it needs
  `speech-dispatcher` (`spd-say`) or `espeak` installed; `willow doctor` will
  tell you if it can't find one.
- Scripts and CI that already know a result can drop a line into
  `.willow/results.txt`, e.g. `PASS npm test` or `1 pytest`, and Willow will
  announce it.
- If you run `willow run` somewhere no Willow is watching, it **says so loudly**
  rather than recording the result where nobody will hear it.

## What this version deliberately does *not* do

- It does **not** interrupt you with a visual description unprompted — you ask
  with `describe`.
- It does **not** let a model decide what you hear. A fixed rulebook does that,
  so it behaves the same every time. See [RULES.md](RULES.md).
- It does **not** change your files, so there is no `undo`. Willow tells you a
  silent revert happened; putting it back is yours to do.

## Running the tests

```bash
willow run python -m unittest discover
```

The suite pins every rule in [RULES.md](RULES.md) to the exact sentence it
produces, and carries a regression test for every defect ever found — named for
what Willow used to say wrongly.

For the repeatable synthetic technical evaluation:

```bash
python willow.py run python evaluate_memory.py --output .willow/evaluation.json
```

This reports precision, recall, F1, repetition, latency and storage on explicit
fixtures plus 110 revisits. It is not a user study. Cross-platform CI is
configured; real NVDA/JAWS/VoiceOver/Orca acceptance remains a study prerequisite.
