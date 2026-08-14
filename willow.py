#!/usr/bin/env python3
"""
Willow - a companion narrator for blind builders vibe-coding with Claude Code.

Willow watches your project folder while an AI coding agent works in it, and
tells you - out loud AND as text your screen reader can read - what the agent
actually did. It reports only checkable FACTS (files changed, builds/tests
passed or failed), never guesses.

Two commands:

    python willow.py watch [folder]     Start watching (run in its own window).
    python willow.py watch --narrate    Same, but say the SAME verified facts in
                                        a more natural voice (needs an API key).
    python willow.py run  <command>     Run a build/test command THROUGH Willow
                                        so it can report the real pass/fail.

While 'watch' is running, press ENTER at any time to hear the current state
("what's the state right now?"), or type q then ENTER to quit.

Design principles:
  * FACTS vs GUESSES: Willow reports only verifiable facts. No AI interpretation.
  * Built from OUTSIDE the agent: Willow watches the files itself, so nothing
    depends on trusting the agent's own account of what it did.
  * Episodes, not spam: changes are grouped into chunks split at natural pauses
    or at a build/test checkpoint.
  * A fixed rulebook decides what you hear (see RULES.md) - the same every time.
  * Push + pull: it speaks when something happens; you can also ask any time.

No third-party libraries required - standard Python 3 only.
"""

import sys
import os
import time
import json
import hashlib
import difflib
import threading
import queue
import subprocess
import platform
import urllib.request

# --------------------------------------------------------------------------
# Settings (plain, reviewable numbers)
# --------------------------------------------------------------------------

POLL_SECONDS = 1.0        # how often Willow looks at the folder
QUIET_SECONDS = 2.5       # a pause this long ends a "chunk" (episode)
STUCK_THRESHOLD = 3       # same file failing this many chunks in a row = "stuck"
MAX_DIFF_BYTES = 2_000_000  # don't line-diff files bigger than this

# Folders/files Willow ignores completely (noise, or its own workspace).
IGNORE_DIRS = {
    ".willow", ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".cache", ".idea", ".vscode", ".pytest_cache",
    ".mypy_cache", "coverage", ".turbo",
}
IGNORE_FILE_SUFFIXES = (".pyc", ".pyo", ".swp", ".tmp", "~")
IGNORE_FILE_NAMES = {".DS_Store", "Thumbs.db"}

WILLOW_DIR = ".willow"
INBOX_NAME = "inbox.jsonl"     # willow run writes verified results here (JSON)
RESULTS_NAME = "results.txt"   # a simple "tray": drop plain pass/fail lines here
LOG_NAME = "willow.log"        # a plain text copy of everything Willow says

# --- Optional LLM narration (see the Narrator class) ----------------------
# Off unless you run 'watch --narrate'. It ONLY rewords Willow's own verified
# facts into a more natural spoken sentence - it never sees your code or the
# agent's account, and Willow always falls back to plain wording if it fails.
NARRATE_MODEL = "claude-haiku-4-5"   # small, fast, cheap - this is only phrasing
NARRATE_MAX_TOKENS = 200
NARRATE_TIMEOUT = 8                  # seconds; falls back to plain wording if slow
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


# --------------------------------------------------------------------------
# Speaking out loud (uses your computer's built-in voice - no internet needed)
# --------------------------------------------------------------------------

class Speaker:
    """Speaks text aloud using the operating system's built-in voice.

    Runs in its own thread so speaking never blocks the watching, and a queue
    keeps announcements from talking over each other.
    """

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.q = queue.Queue()
        self.system = platform.system()  # 'Windows', 'Darwin' (mac), 'Linux'
        self._warned = False
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()

    def say(self, text):
        if self.enabled:
            self.q.put(text)

    def _worker(self):
        while True:
            text = self.q.get()
            try:
                self._speak_blocking(text)
            except Exception:
                # If speaking fails, stay silent about it after one note; the
                # on-screen text is always there as a fallback.
                if not self._warned:
                    self._warned = True
                    print("Willow: (note) could not use the voice on this "
                          "computer; text updates will still appear.")

    def _speak_blocking(self, text):
        one_line = " ".join(text.split())
        if self.system == "Windows":
            escaped = one_line.replace("'", "''")
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.Rate = 1; "
                f"$s.Speak('{escaped}')"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif self.system == "Darwin":
            subprocess.run(["say", one_line],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:  # Linux and others
            subprocess.run(["espeak", one_line],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --------------------------------------------------------------------------
# Optional: giving Willow a more natural voice (the narrator)
# --------------------------------------------------------------------------

class Narrator:
    """Turns Willow's VERIFIED facts into a natural spoken sentence.

    This is a WORDING layer only, and it is off unless you ask for it. It is
    handed the exact factual line Willow already built from watching the folder
    (files changed, +/- lines, pass/fail, silent undos) and asked to say the
    same thing more naturally. It never sees your code or the agent's own
    account, so it cannot invent a result - it can only re-word facts Willow
    already checked. If it is unavailable for any reason (no key, offline, an
    error, a slow reply), Willow silently falls back to the plain factual
    sentence: it never blocks and never guesses.

    Uses only the Python standard library (urllib), so Willow keeps its
    "nothing to install" promise even with narration switched on.
    """

    SYSTEM = (
        "You are the voice of Willow, a tool that tells a blind developer what "
        "an AI coding agent just did to their project. You will be given a line "
        "of VERIFIED FACTS that Willow checked for itself. Rephrase it into one "
        "or two short, natural spoken sentences for a screen-reader user. "
        "Rules: say ONLY what the facts state. Do not add, guess, soften, or "
        "invent anything - in particular, never say a build or test passed or "
        "failed unless the facts say so. Keep file names and numbers exact. No "
        "preamble and no sign-off - just the spoken sentence."
    )

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.api_key = self._find_key()
        self._warned = False
        if self.enabled and not self.api_key:
            print("Willow: (note) narration is on, but I couldn't find an API "
                  "key. Set ANTHROPIC_API_KEY, or put your key in a file called "
                  ".willow_key in your home folder. Until then I'll use plain "
                  "wording.")
            self.enabled = False

    def _find_key(self):
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if key:
            return key
        try:
            path = os.path.join(os.path.expanduser("~"), ".willow_key")
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def narrate(self, facts):
        """Return a natural rewording of `facts`, or None to fall back to plain."""
        if not self.enabled or not self.api_key:
            return None
        body = json.dumps({
            "model": NARRATE_MODEL,
            "max_tokens": NARRATE_MAX_TOKENS,
            "system": self.SYSTEM,
            "messages": [{"role": "user", "content": facts}],
        }).encode("utf-8")
        req = urllib.request.Request(
            ANTHROPIC_URL, data=body, method="POST",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=NARRATE_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            # offline, bad key, timeout, anything at all -> silent fallback
            if not self._warned:
                self._warned = True
                print("Willow: (note) natural narration is unavailable right "
                      "now (offline, or the service didn't answer); using plain "
                      "wording. I won't mention this again.")
            return None
        # If the model declined or returned nothing usable, fall back to plain.
        if data.get("stop_reason") == "refusal":
            return None
        pieces = [b.get("text", "") for b in data.get("content", [])
                  if b.get("type") == "text"]
        text = " ".join(p.strip() for p in pieces if p.strip())
        return text or None


# --------------------------------------------------------------------------
# Looking at the folder (Willow's "eyes")
# --------------------------------------------------------------------------

def is_ignored(relpath):
    parts = relpath.replace("\\", "/").split("/")
    for p in parts[:-1]:
        if p in IGNORE_DIRS:
            return True
    name = parts[-1]
    if name in IGNORE_FILE_NAMES:
        return True
    if name.endswith(IGNORE_FILE_SUFFIXES):
        return True
    return False


def scan(folder):
    """Take a snapshot of the folder: {relative_path: file_info}.

    file_info = {"size", "mtime", "hash", "text"}  (text is None if not text
    or too big to diff).
    """
    snap = {}
    for root, dirs, files in os.walk(folder):
        # prune ignored directories in place so os.walk skips them
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, folder)
            if is_ignored(rel):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            info = {"size": st.st_size, "mtime": st.st_mtime,
                    "hash": None, "text": None}
            try:
                with open(full, "rb") as f:
                    data = f.read()
                info["hash"] = hashlib.sha1(data).hexdigest()
                if len(data) <= MAX_DIFF_BYTES:
                    try:
                        info["text"] = data.decode("utf-8")
                    except UnicodeDecodeError:
                        info["text"] = None  # binary file
            except OSError:
                # file may be mid-write or locked; skip this tick
                continue
            snap[rel] = info
    return snap


def line_delta(old_text, new_text):
    """Return (added_lines, removed_lines) between two text blobs."""
    old_lines = old_text.splitlines() if old_text else []
    new_lines = new_text.splitlines() if new_text else []
    added = removed = 0
    for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def parse_result_line(line):
    """Turn one line of the results tray into a check, or None if unreadable.

    Accepts:  'PASS npm test'  /  'FAIL build'  /  '0 pytest'  /  '1 npm test'
    The number is a real exit code (0 = passed, anything else = failed).
    """
    line = line.strip()
    if not line:
        return None
    parts = line.split(None, 1)
    token = parts[0].upper()
    label = parts[1].strip() if len(parts) > 1 else "the check"
    if token in ("PASS", "PASSED", "OK", "SUCCESS"):
        code = 0
    elif token in ("FAIL", "FAILED", "ERROR"):
        code = 1
    else:
        try:
            code = int(parts[0])
        except ValueError:
            return None
    return {"type": "check", "command": label, "exit_code": code}


def describe_change(rel, base_info, now_info):
    """One short human phrase describing the net change to one file."""
    if base_info is None and now_info is not None:
        return f"created {rel}"
    if base_info is not None and now_info is None:
        return f"deleted {rel}"
    # modified
    if base_info and now_info and base_info.get("text") is not None \
            and now_info.get("text") is not None:
        added, removed = line_delta(base_info["text"], now_info["text"])
        bits = []
        if added:
            bits.append(f"+{added}")
        if removed:
            bits.append(f"-{removed}")
        detail = " ".join(bits) if bits else "reformatted"
        return f"edited {rel} ({detail} lines)"
    return f"changed {rel}"


# --------------------------------------------------------------------------
# The watcher (Willow's brain) - groups changes into episodes and announces
# --------------------------------------------------------------------------

class Willow:
    def __init__(self, folder, speaker, narrator=None):
        self.folder = os.path.abspath(folder)
        self.speaker = speaker
        self.narrator = narrator  # optional: rewords facts more naturally
        self.log_path = os.path.join(self.folder, WILLOW_DIR, LOG_NAME)
        self.inbox_path = os.path.join(self.folder, WILLOW_DIR, INBOX_NAME)
        self.results_path = os.path.join(self.folder, WILLOW_DIR, RESULTS_NAME)
        self._inbox_offset = 0
        self._results_offset = 0

        self.session_baseline = scan(self.folder)  # state when watching began
        self.committed = dict(self.session_baseline)  # state at last episode end
        self.prev = dict(self.session_baseline)       # state at previous tick

        # current episode being built up
        self.ep_touched = {}   # rel -> number of writes seen this episode
        self.last_change_at = None

        self.fail_streak = {}  # rel -> consecutive failing episodes
        self.stuck_announced = set()
        self.stop = threading.Event()

    # ---- output -----------------------------------------------------------

    def announce(self, text, narratable=False):
        # `text` is ALWAYS the verified factual line. If narration is on and
        # this is a change report, we speak a natural rewording of the same
        # facts; otherwise we speak the facts as-is. Either way the exact
        # factual line is written to the log as the citable ground truth.
        spoken = text
        if narratable and self.narrator is not None:
            alt = self.narrator.narrate(text)
            if alt:
                spoken = alt
        print("Willow: " + spoken, flush=True)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + text + "\n")
                if spoken != text:
                    f.write(time.strftime("%H:%M:%S ") + "(spoken) "
                            + spoken + "\n")
        except OSError:
            pass
        self.speaker.say("Willow. " + spoken)

    # ---- main loop --------------------------------------------------------

    def run(self):
        self.announce(
            f"watching {os.path.basename(self.folder)}. "
            "I'll tell you what the agent changes. "
            "Press Enter any time to hear the current state.")
        while not self.stop.is_set():
            time.sleep(POLL_SECONDS)
            try:
                self._tick()
            except Exception as e:
                print(f"Willow: (internal note) {e}", flush=True)

    def _tick(self):
        now = scan(self.folder)

        # 1) note any files that changed since the previous look (write churn)
        changed_now = self._diff_paths(self.prev, now)
        if changed_now:
            for rel in changed_now:
                self.ep_touched[rel] = self.ep_touched.get(rel, 0) + 1
            self.last_change_at = time.time()
        self.prev = now

        # 2) did a build/test result arrive? (a checkpoint) -> close episode now
        check = self._read_new_check()
        if check is not None:
            self._close_episode(now, check)
            return

        # 3) otherwise close the episode after a natural pause
        if self.ep_touched and self.last_change_at is not None:
            if time.time() - self.last_change_at >= QUIET_SECONDS:
                self._close_episode(now, None)

    def _diff_paths(self, before, after):
        changed = set()
        for rel, info in after.items():
            if rel not in before or before[rel]["hash"] != info["hash"]:
                changed.add(rel)
        for rel in before:
            if rel not in after:
                changed.add(rel)  # deleted
        return changed

    # ---- reading build/test results written by `willow run` ---------------

    def _read_new_check(self):
        # A build/test result can arrive two ways, both watched here:
        #   (1) inbox.jsonl  - verified exit codes written by `willow run`
        #   (2) results.txt  - a simple tray anyone can drop a pass/fail into
        result = self._read_new_json_checks(None)
        result = self._read_new_text_checks(result)
        return result

    def _read_new_json_checks(self, result):
        try:
            if not os.path.exists(self.inbox_path):
                return result
            with open(self.inbox_path, "r", encoding="utf-8") as f:
                f.seek(self._inbox_offset)
                lines = f.readlines()
                self._inbox_offset = f.tell()
        except OSError:
            return result
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                result = json.loads(line)  # keep the latest one this tick
            except json.JSONDecodeError:
                continue
        return result

    def _read_new_text_checks(self, result):
        try:
            if not os.path.exists(self.results_path):
                return result
            with open(self.results_path, "r", encoding="utf-8") as f:
                f.seek(self._results_offset)
                lines = f.readlines()
                self._results_offset = f.tell()
        except OSError:
            return result
        for line in lines:
            parsed = parse_result_line(line)
            if parsed is not None:
                result = parsed  # keep the latest one this tick
        return result

    # ---- closing a chunk and deciding what to say (the RULEBOOK) ----------

    def _close_episode(self, now, check):
        touched = self.ep_touched
        self.ep_touched = {}
        self.last_change_at = None

        parts = []

        # (a) file changes: report net change per file + churn + silent undo
        reverted = []
        if touched:
            file_phrases = []
            for rel in sorted(touched):
                base = self.committed.get(rel)
                cur = now.get(rel)
                writes = touched[rel]

                # silent undo: many writes but ends where it started
                base_hash = base["hash"] if base else None
                cur_hash = cur["hash"] if cur else None
                orig = self.session_baseline.get(rel)
                orig_hash = orig["hash"] if orig else None

                if writes >= 2 and cur_hash == base_hash:
                    reverted.append(rel)
                    continue
                if cur_hash == orig_hash and base_hash != orig_hash:
                    reverted.append(f"{rel} (back to its earlier version)")
                    continue

                phrase = describe_change(rel, base, cur)
                if writes >= 3:
                    phrase += f", {writes} times"
                file_phrases.append(phrase)

            if file_phrases:
                if len(file_phrases) == 1:
                    parts.append(file_phrases[0])
                else:
                    parts.append(f"{len(file_phrases)} files: "
                                 + "; ".join(file_phrases))

        # (b) silent undo call-out (behaviourally important even if net-zero)
        for r in reverted:
            parts.append(f"quietly undid the change to {r}")

        # (c) build/test outcome (a hard fact from a real exit code)
        passed = None
        if check is not None:
            passed = (check.get("exit_code") == 0)
            cmd = check.get("command", "the check")
            if passed:
                parts.append(f"then '{cmd}' passed")
            else:
                code = check.get("exit_code")
                parts.append(f"then '{cmd}' FAILED (exit code {code})")

        # (d) derived "stuck" signal: repeated writes across failing episodes
        if check is not None:
            if passed:
                for rel in touched:
                    self.fail_streak[rel] = 0
                    self.stuck_announced.discard(rel)
            else:
                for rel in touched:
                    self.fail_streak[rel] = self.fail_streak.get(rel, 0) + 1
        stuck_notes = []
        for rel, n in self.fail_streak.items():
            if n >= STUCK_THRESHOLD and rel not in self.stuck_announced:
                stuck_notes.append(f"the agent seems stuck on {rel} "
                                   f"({n} failing attempts in a row)")
                self.stuck_announced.add(rel)

        # commit new baseline and speak, if there is anything worth saying
        self.committed = dict(now)
        if parts:
            self.announce(". ".join(parts) + ".", narratable=True)
        for note in stuck_notes:
            self.announce(note + ".")

    # ---- the "what's the state right now?" answer (pull) ------------------

    def state_summary(self):
        now = scan(self.folder)
        created = [r for r in now if r not in self.session_baseline]
        deleted = [r for r in self.session_baseline if r not in now]
        modified = [r for r in now
                    if r in self.session_baseline
                    and now[r]["hash"] != self.session_baseline[r]["hash"]]
        bits = []
        if created:
            bits.append(f"{len(created)} new file(s)")
        if modified:
            bits.append(f"{len(modified)} changed file(s)")
        if deleted:
            bits.append(f"{len(deleted)} deleted file(s)")
        if not bits:
            msg = "state right now: nothing has changed since we started."
        else:
            msg = "state right now: " + ", ".join(bits) + " since we started"
            stuck = [r for r, n in self.fail_streak.items()
                     if n >= STUCK_THRESHOLD]
            if stuck:
                msg += ". Possibly stuck on: " + ", ".join(stuck)
            msg += "."
        self.announce(msg, narratable=True)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def find_willow_dir(start):
    """Find the nearest .willow folder from 'start' upward; else make one here."""
    cur = os.path.abspath(start)
    while True:
        candidate = os.path.join(cur, WILLOW_DIR)
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def cmd_watch(argv):
    # pull the optional --narrate flag out of the arguments
    narrate = False
    positional = []
    for a in argv:
        if a in ("--narrate", "-n"):
            narrate = True
        else:
            positional.append(a)

    folder = positional[0] if positional else "."
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        print(f"Willow: that folder does not exist: {folder}")
        return 1
    os.makedirs(os.path.join(folder, WILLOW_DIR), exist_ok=True)

    speaker = Speaker(enabled=True)
    narrator = Narrator(enabled=True) if narrate else None
    if narrator is not None and narrator.enabled:
        print("Willow: natural narration is on - I'll say the same verified "
              "facts in a more natural way (the exact facts are still saved).")
    willow = Willow(folder, speaker, narrator=narrator)

    worker = threading.Thread(target=willow.run, daemon=True)
    worker.start()

    # main thread listens for you: Enter = state, q = quit
    try:
        for line in sys.stdin:
            cmd = line.strip().lower()
            if cmd in ("q", "quit", "exit"):
                break
            if cmd in ("", "state", "status", "s"):
                willow.state_summary()
            elif cmd in ("help", "h", "?"):
                print("Willow: press Enter for current state, or type q to quit.")
            else:
                willow.state_summary()
    except KeyboardInterrupt:
        pass
    finally:
        willow.stop.set()
        print("Willow: stopped watching. Goodbye.")
    return 0


def cmd_run(argv):
    if not argv:
        print("Willow: tell me what to run, e.g. python willow.py run npm test")
        return 1

    willow_dir = find_willow_dir(".")
    if willow_dir is None:
        # not watching yet; still run the command, just no inbox to notify
        willow_dir = os.path.join(os.path.abspath("."), WILLOW_DIR)
        os.makedirs(willow_dir, exist_ok=True)

    command_str = " ".join(argv)
    print(f"Willow: running '{command_str}' ...", flush=True)
    start = time.time()
    try:
        proc = subprocess.run(argv)
        exit_code = proc.returncode
    except FileNotFoundError:
        print(f"Willow: could not find that command: {argv[0]}")
        exit_code = 127
    except KeyboardInterrupt:
        print("Willow: the command was cancelled.")
        exit_code = 130
    took = round(time.time() - start, 1)

    record = {"ts": time.time(), "type": "check",
              "command": command_str, "exit_code": exit_code, "seconds": took}
    try:
        with open(os.path.join(willow_dir, INBOX_NAME), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass

    verdict = "passed" if exit_code == 0 else f"FAILED (exit code {exit_code})"
    print(f"Willow: '{command_str}' {verdict}. "
          "The watching window will announce this too.")
    return exit_code


HELP = """Willow - a companion narrator for blind builders using Claude Code.

Usage:
  python willow.py watch [folder]     Start watching a project folder.
                                      (Leave [folder] out to watch this folder.)
  python willow.py watch --narrate    Watch, but say the SAME verified facts in
                                      a more natural voice. Needs an API key in
                                      ANTHROPIC_API_KEY (or a .willow_key file in
                                      your home folder). Falls back to plain
                                      wording if it's ever unavailable.
  python willow.py run  <command>     Run a build/test command through Willow
                                      so it can report the real pass/fail,
                                      e.g. python willow.py run npm test

While watching: press Enter to hear the current state; type q to quit.
"""


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(HELP)
        return 0
    cmd = sys.argv[1].lower()
    rest = sys.argv[2:]
    if cmd == "watch":
        return cmd_watch(rest)
    if cmd == "run":
        return cmd_run(rest)
    print(HELP)
    return 1


if __name__ == "__main__":
    sys.exit(main())
