# Willow setup with a screen reader

Willow's project memory and command interface work on Windows, macOS and Linux.
You can use every factual feature without speech, an AI account, or Git.
Python 3.8 or newer is required. A current supported Python is preferable.
These instructions install the source checkout; a published package can lag it.

## Windows: NVDA or JAWS

1. Open PowerShell in the downloaded Willow folder. You can navigate to it with
   `cd "C:\Path\To\willow"`. Keep quotes around paths containing spaces.
2. Check Python with `py -3 --version`. If it is missing, install Python from
   [python.org](https://www.python.org/downloads/) and reopen PowerShell.
3. Create a local environment: `py -3 -m venv .venv`.
4. Install Willow: `.venv\Scripts\python.exe -m pip install .`.
5. Set up your project: `.venv\Scripts\willow.exe init "C:\Path\To\Project" --no-voice`.
   This preserves existing agent instructions and merges Claude hooks. For other
   agents, add `--no-hooks` and tell the agent to run checks through Willow.
6. Check the setup: `.venv\Scripts\willow.exe doctor "C:\Path\To\Project" --no-voice`.
   Read any reported problems using your usual terminal review commands.
7. Start: `.venv\Scripts\willow.exe watch "C:\Path\To\Project" --no-voice`.
   Wait for the line beginning “watching”. Text-only mode avoids a second voice;
   NVDA or JAWS can read the terminal. Willow does not move focus.
8. Open another PowerShell window for your AI agent. Keep the watcher open.
   Type `help` in its window to list commands. Press Enter for state; type `q`
   to save the final checkpoint and quit.

For Willow's own on-demand speech, omit `--no-voice`. System speech is available
without additional Python packages. To route speech through NVDA, obtain the
architecture-matched controller client from
[NV Access's official releases and instructions](https://github.com/nvaccess/nvda/blob/master/extras/controllerClient/readme.md).
For 64-bit Python use the x64 DLL; ARM64 Python requires the ARM64 DLL.
Start NVDA and add `--backend nvda --nvda-dll "C:\Path\nvdaControllerClient.dll"`
to `watch`. You can save `speech_backend` and `nvda_dll` in the project config.
Willow does not download or bundle the DLL. NVDA owns its rate and voice settings.

For installed, running JAWS, use `--backend jaws`. Willow attempts its active
`FreedomSci.JawsApi` COM interface through Windows PowerShell. If unavailable,
it prints an explicit fallback to system speech. Validate this on the actual
JAWS version before a study; mocked tests are not hardware acceptance.
JAWS owns its voice settings too.

## macOS: VoiceOver

1. Open Terminal and go to the Willow checkout: `cd "/path/to/willow"`.
2. Check Python: `python3 --version`. Install it if unavailable.
3. Create an environment: `python3 -m venv .venv`.
4. Install: `.venv/bin/python -m pip install .`.
5. Set up: `.venv/bin/willow init "/path/to/project" --no-voice`.
6. Check: `.venv/bin/willow doctor "/path/to/project" --no-voice`.
7. Start: `.venv/bin/willow watch "/path/to/project" --no-voice`.

VoiceOver reads the terminal using your existing commands. Omit `--no-voice`
for native macOS `say` speech. It is a separate voice, not a VoiceOver speech
queue. Keep on-demand mode when overlap is distracting. Use your preferred
terminal accessibility settings; Willow requires no mouse interaction.

## Linux: Orca or another screen reader

Follow the macOS commands above, using your Linux terminal and project paths.
If creating the environment reports a missing `venv` module, install your
distribution's Python venv package first.

Text-only mode works without a speech engine. For Willow's speech, install
Speech Dispatcher (`spd-say`) or eSpeak (`espeak`) using your distribution's
package manager. For example, on Debian/Ubuntu:

```sh
sudo apt install python3-venv speech-dispatcher espeak
```

Then omit `--no-voice`. Willow tries `spd-say` and falls back to `espeak` if the
first program is missing or returns an error. Orca speech activity is not
monitored; on-demand mode gives you control over when Willow talks. A desktop
session and working audio service are needed for speech, but not for memory.

## Returning to work

The first watch saves a baseline. On subsequent starts you receive a short
briefing with evidence numbers. Ask `briefing all` for remaining changes.
Type `history src/example.py` to review a file, or `evidence 12` to inspect a
specific source and its hashes. Filenames after `history` may contain spaces.

Before a break type `away`. Willow saves a break baseline and pauses automatic
speech while continuing to record. Type `back` to hear the net changes, then
`briefing` to repeat them. Typing `away` twice preserves the first baseline.

Use `stop` to cancel queued speech. `pause` selects on-demand speech; `resume`
enables automatic speech at file-edit pauses. `quiet` filters routine automatic
speech; `loud` restores it. `faster` and `slower` change the system voice rate.
These interactive choices last for the running watcher; edit `.willow/config.json`
to keep preferences across runs. `--no-voice` stays text-only throughout.

## Checking speech and limitations

Run `doctor` without `--no-voice` to request an audible check. Confirm you hear
it, can stop it, and can still operate your screen reader. Engine availability
does not prove audible output. See the acceptance worksheet in the study guide.

The optional `describe` screen capture still supports Windows only. Project
memory, briefings, provenance, build/test results, text and system speech support
all three platforms. A native macOS/Linux screenshot integration needs separate
implementation and device validation; do not advertise it as working.

Memory and logs stay in `.willow/`. Stop the watcher before backing it up or
removing it. Removing this directory also removes your project settings and
history. `--no-memory` avoids recording session memory without erasing old data.
No telemetry is uploaded. Optional cloud narration and visual descriptions
send data to the configured provider; leave those off for a local-only setup.
