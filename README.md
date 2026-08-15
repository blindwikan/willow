# Willow

**Willow is a companion narrator for blind builders using AI coding tools (Claude Code).**

While an AI agent edits your project, Willow watches the folder and tells you —
**out loud and in text** — what the agent actually did: which files changed,
what changed in them, whether builds/tests passed or failed, and whether the
agent quietly undid its own work. Every update is a **fact you could check
yourself**. Willow never guesses.

---

## What you need

- **Python 3** installed. Check by opening a terminal and typing:

  ```bash
  python --version
  ```

  If you see a version number (e.g. `Python 3.14`), you're ready. Nothing else
  to install — Willow uses only what comes with Python and your computer's
  built-in voice.

## How to use it (two windows)

Willow runs alongside Claude Code. You'll have **two windows open**.

### Window 1 — start Willow watching

Open a terminal, go to your project folder, and start Willow:

```bash
python willow.py watch
```

Willow says *"watching … I'll tell you what the agent changes."* Leave this
window open. From now on, whenever the AI changes files, Willow speaks up.

- **Press Enter** at any time to hear **the state right now** ("2 new files, 1
  changed since we started"). Handy if you missed something.
- **Type `q` then Enter** to stop.

> Tip: to watch a different folder, put its path after `watch`, e.g.
> `python willow.py watch "C:\my project"`.

### Window 2 — do your vibe coding, and check your code *through* Willow

Work with Claude Code as normal in your second window. When you want to
**build or test** your code, run that command **through Willow** so it can
report the true result:

```bash
python willow.py run npm test
```

or

```bash
python willow.py run python -m pytest
```

Willow runs the command, shows the result, and the watching window announces
**"… then 'npm test' passed"** or **"… FAILED"**. (You only need `willow run`
for build/test commands. Everyday file changes are noticed automatically.)

### Catching the tests the AI runs by itself

Willow watches the *folder*, so if Claude Code runs a test **inside its own
session**, Willow sees the files change but not the pass/fail. To fix that, put a
small **`CLAUDE.md`** file in your project (there's a ready-made one in
`testfolder`) telling Claude Code to run its build/test commands *through* Willow.
Then the AI's own tests are captured with a **real result** — not the AI's word
for it. If the AI forgets, Willow just stays quiet about pass/fail; it never makes
one up.

Scripts or CI that already know the result can also drop a line into
`.willow/results.txt`, e.g. `PASS npm test` or `1 pytest`, and Willow will
announce it.

---

## What Willow will say — examples

- *"created login.js."*
- *"2 files: edited login.js (+12 -3 lines); edited styles.css (+4 lines). then 'npm test' passed."*
- *"quietly undid the change to app.js."*  ← a silent undo you'd normally miss
- *"the agent seems stuck on login.js (3 failing attempts in a row)."*
- *"state right now: 3 new files, 1 changed file since we started."*

## Good to know

- Willow keeps a small private folder called `.willow` inside your project (for
  its own notes and a text log at `.willow/willow.log`). It **never touches your
  real files** — it only looks at them.
- It ignores noise like `node_modules`, `.git`, and build output.
- It works on **Windows and Mac** using the computer's built-in voice, no
  internet needed.

## Optional: a more natural voice

By default, Willow words its announcements with a fixed rulebook (no internet, no
account needed). If you'd like it to say the **same facts** in a more natural,
spoken way, add `--narrate`:

```bash
python willow.py watch --narrate
```

- This mode asks a small language model to **re-word Willow's own verified
  facts** — it never sees your code and can't invent anything. The exact facts
  are still saved to the log.
- It needs an **Anthropic API key** and internet **only in this mode**. Put your
  key in the `ANTHROPIC_API_KEY` environment variable, or in a file called
  `.willow_key` in your home folder. (It is never stored in this project.)
- If the key is missing or the service is unavailable, Willow just uses the plain
  wording — it never stops working.

The normal `watch` (above) needs none of this. `--narrate` is entirely optional.

## Optional: hear what your app *looks* like

While Willow is watching, type **`describe`** and press Enter, and Willow will
tell you how the app you're building **looks** right now:

```
describe
```

- Willow **finds your app's browser window by itself** — you don't have to aim
  it or leave the terminal.
- It **names the window it grabbed first** (a fact — e.g. *"Chrome — 'Welcome —
  Sign in'"*), then describes how it looks, clearly labelled as an
  **interpretation, not a checked fact**. So if it ever grabbed the wrong window,
  you'd hear the wrong name and know to ignore the rest.
- If it can't find your app, or the window is minimised, or there's no key, it
  **says so plainly** — it never invents a description.

Have your app **open in a browser** (Chrome, Edge, Firefox…) before you type
`describe`. Like `--narrate`, this uses your **Anthropic API key** (a picture has
to be looked at). **Windows only for now** — other systems are planned.

## What this first version does *not* do (on purpose)

- It does **not** *automatically* interrupt you with a visual description — you
  ask for it with `describe`. A proactive "nudge" is planned for a later version.
- It does **not** use any AI to decide what to tell you — a fixed rulebook does
  that, so it behaves the same every time. See [RULES.md](RULES.md).
