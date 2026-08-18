# Willow

**Willow is a companion narrator for blind builders working with AI coding
agents.**

While an AI agent edits your project, Willow watches the folder and tells you —
**out loud and in text** — what the agent actually did: which files changed,
what changed in them, whether builds and tests really passed or failed, and
whether the agent quietly undid its own work.

Every update is a **fact you could check yourself**. Willow never guesses. If it
cannot verify something, it says so, or says nothing — it never fills the gap.

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
filled in), adds `.willow/` to your `.gitignore`, and installs Claude Code hooks.

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

Willow says *"starting up"*, then *"watching … I'll tell you what the agent
changes."* **Wait for that second line** — anything that changes before it counts
as how the project already was. Leave this window open.

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

The exact facts always go to the log as the citable ground truth, with the
spoken version beside them.

---

## Optional: run the model on your own machine

Willow works with **Anthropic**, **OpenAI**, or a **local model through Ollama** —
and with anything else that speaks the OpenAI API (LM Studio, vLLM, llama.cpp,
OpenRouter…).

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

`voice_rate` runs from -10 to 10. Command-line flags beat the config file, which
beats environment variables (`WILLOW_PROVIDER`, `WILLOW_BASE_URL`,
`WILLOW_TEXT_MODEL`, `WILLOW_VISION_MODEL`, `WILLOW_VOICE_RATE`), which beat the
defaults.

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
