#!/usr/bin/env python3
"""
Willow - a companion narrator for blind builders working with AI coding agents.

Willow watches your project folder while an AI coding agent works in it, and
tells you - out loud AND as text your screen reader can read - what the agent
actually did. It reports only checkable FACTS (files changed, builds and tests
passed or failed), never guesses.

    willow init                  Set this project up (one time).
    willow watch                 Start watching (run in its own window).
    willow run <command>         Run a build/test command through Willow so it
                                 can report the real pass/fail.
    willow doctor                Check everything is working, and say so out loud.

While 'watch' is running, type any of these and press Enter:

    (nothing)   what's the state right now?
    diff        read out what changed in the last chunk
    recap       summary of the whole session so far
    describe    what does my app look like right now?
    stop        stop talking (also: shh)
    faster / slower     change the speaking rate
    quiet / loud        speak everything, or only the important things
    help        list these
    q           quit

Design principles:
  * FACTS vs GUESSES. Willow reports only verifiable facts. If it cannot verify
    something it says so, or says nothing - it never fills the gap with a guess.
  * Built from OUTSIDE the agent, so nothing depends on trusting the agent's own
    account of what it did.
  * Episodes, not spam: changes are grouped into chunks split at natural pauses
    or at a build/test checkpoint.
  * A fixed rulebook decides what you hear (see RULES.md) - the same every time.
  * Push + pull: it speaks when something happens; you can also ask any time.

Optional natural narration and visual descriptions can run on Anthropic, OpenAI,
or a LOCAL model through Ollama (no key, no internet, nothing leaves your
machine). Whatever the model says is checked against the verified facts before
it is spoken - see `faithful()` below.

No third-party libraries required - standard Python 3 only.
"""

import sys
import os
import re
import io
import time
import json
import shutil
import hashlib
import difflib
import threading
import queue
import subprocess
import platform
import base64
import urllib.request
import urllib.error

__version__ = "2.0"

# --------------------------------------------------------------------------
# Defaults (every one of these can be changed in .willow/config.json)
# --------------------------------------------------------------------------

DEFAULTS = {
    # --- watching ---
    "poll_seconds": 0.7,        # how often Willow looks at the folder
    "quiet_seconds": 2.5,       # a pause this long ends a "chunk" (episode)
    "stuck_threshold": 3,       # same file + same failing check = "stuck"
    "extra_ignore_dirs": [],    # folder names to ignore on top of the built-ins

    # --- voice ---
    "voice": True,              # speak out loud at all
    "voice_rate": 3,            # -10 (slowest) .. 10 (fastest). Screen-reader
                                # users usually want this well above 0.
    "voice_host": True,         # Windows: keep one voice process alive instead
                                # of starting one per sentence (much faster to
                                # the first word). Willow tests it at startup
                                # and quietly falls back if it misbehaves.
    "verbosity": "normal",      # "normal" or "quiet" (quiet = speak only the
                                # important things; everything is still printed)

    # --- optional model use (narration + describe) ---
    "provider": "auto",         # auto | anthropic | openai | ollama |
                                #   openai-compat | none
    "base_url": None,           # override the provider's endpoint
    "text_model": None,         # None = the provider picks a sensible default
    "vision_model": None,
    "narrate_timeout": 1.5,     # HARD budget. Late narration is worse than none.
    "view_timeout": 30,         # a picture is allowed to take longer

    # --- housekeeping ---
    "max_log_bytes": 2_000_000,  # rotate willow.log past this
    "keep_captures": False,      # keep describe screenshots on disk afterwards
}

MAX_DIFF_BYTES = 2_000_000       # don't keep text for files bigger than this
MAX_BASELINE_BYTES = 96_000_000  # total file text held for diffing
VIEW_MAX_WIDTH = 1400            # shrink big screenshots to keep cost/time down
HEARTBEAT_SECONDS = 5            # watcher refreshes watching.json this often
WATCHER_STALE_SECONDS = 30       # older than this = probably not running

# Folders Willow ignores completely (noise, or its own workspace).
IGNORE_DIRS = {
    ".willow", ".git", ".hg", ".svn", "node_modules", "__pycache__",
    ".venv", "venv", "env", "dist", "build", ".next", ".nuxt", ".svelte-kit",
    ".cache", ".parcel-cache", ".idea", ".vscode", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", "coverage", "htmlcov", ".turbo",
    ".gradle", ".terraform", "vendor", "Pods", ".dart_tool", ".angular",
    ".serverless", ".nyc_output", "site-packages",
}
IGNORE_FILE_SUFFIXES = (".pyc", ".pyo", ".swp", ".tmp", "~", ".lock")
IGNORE_FILE_NAMES = {".DS_Store", "Thumbs.db"}

WILLOW_DIR = ".willow"
INBOX_NAME = "inbox.jsonl"      # verified results written by `willow run`/hooks
RESULTS_NAME = "results.txt"    # a simple "tray": drop plain pass/fail lines here
LOG_NAME = "willow.log"         # a plain text copy of everything Willow says
EVENTS_NAME = "events.jsonl"    # structured log, for analysis
WATCHING_NAME = "watching.json"  # written by `watch` so `run` can find it

# Browser processes `describe` will look for (the window showing your app):
VIEW_BROWSERS = ("chrome", "msedge", "firefox", "brave", "opera",
                 "vivaldi", "arc", "chromium")

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"


# ==========================================================================
# Configuration
# ==========================================================================

def user_config_dir():
    return os.path.join(os.path.expanduser("~"), ".willow")


def load_config(folder, overrides=None):
    """Defaults < ~/.willow/config.json < <project>/.willow/config.json < env
    < command-line overrides."""
    cfg = dict(DEFAULTS)
    for path in (os.path.join(user_config_dir(), "config.json"),
                 os.path.join(folder, WILLOW_DIR, "config.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg.update({k: v for k, v in loaded.items() if k in DEFAULTS})
        except (OSError, json.JSONDecodeError):
            pass

    env_map = {
        "WILLOW_PROVIDER": "provider",
        "WILLOW_BASE_URL": "base_url",
        "WILLOW_TEXT_MODEL": "text_model",
        "WILLOW_VISION_MODEL": "vision_model",
        "WILLOW_VOICE_RATE": "voice_rate",
        "WILLOW_VERBOSITY": "verbosity",
    }
    for env, key in env_map.items():
        val = os.environ.get(env)
        if val:
            cfg[key] = int(val) if key == "voice_rate" and val.lstrip("-").isdigit() else val

    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


# ==========================================================================
# Model providers - Anthropic, OpenAI, Ollama, or anything OpenAI-compatible
# ==========================================================================
#
# Two transports cover every backend worth supporting: Anthropic's Messages
# API has its own shape, and essentially everything else speaks OpenAI Chat
# Completions. So a single OpenAI-compatible provider with a configurable
# base URL covers OpenAI, Ollama, LM Studio, vLLM, llama.cpp, OpenRouter and
# friends - new backends are configuration, not code.
#
# EVERY method returns None on any failure whatsoever. Willow then falls back
# to its own plain wording. It never blocks and never guesses.

def _post_json(url, headers, payload, timeout):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers=dict(headers, **{"content-type": "application/json"}))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url, timeout, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def read_key_file(name):
    """Read a key from ~/.willow/<name> or the legacy ~/.willow_key.

    Never read from inside the project folder, so a key can't end up in the repo.
    """
    for path in (os.path.join(user_config_dir(), name),
                 os.path.join(os.path.expanduser("~"), ".willow_key")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                key = f.read().strip()
            if key:
                return key
        except OSError:
            continue
    return None


class Provider:
    """What Willow needs from a model. Nothing more."""

    name = "none"
    label = "no model"

    def available(self):
        return False

    def text(self, system, user, max_tokens, timeout):
        return None

    def vision(self, system, prompt, png_bytes, max_tokens, timeout):
        return None

    def supports_vision(self):
        return False

    def problem(self):
        """A plain-English reason this provider can't be used, or None."""
        return "no model backend is configured"


class AnthropicProvider(Provider):
    name = "anthropic"
    DEFAULT_TEXT = "claude-haiku-4-5"
    DEFAULT_VISION = "claude-haiku-4-5"
    VERSION = "2023-06-01"

    def __init__(self, api_key=None, base_url=None, text_model=None, vision_model=None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip() \
            or read_key_file("anthropic_key")
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.text_model = text_model or self.DEFAULT_TEXT
        self.vision_model = vision_model or self.DEFAULT_VISION
        self.label = f"Anthropic ({self.text_model})"

    def available(self):
        return bool(self.api_key)

    def problem(self):
        if not self.api_key:
            return ("no Anthropic API key. Set ANTHROPIC_API_KEY, or put your "
                    "key in " + os.path.join(user_config_dir(), "anthropic_key"))
        return None

    def supports_vision(self):
        return True

    def _headers(self):
        return {"x-api-key": self.api_key, "anthropic-version": self.VERSION}

    def _extract(self, data):
        if data.get("stop_reason") == "refusal":
            return None
        pieces = [b.get("text", "") for b in data.get("content", [])
                  if b.get("type") == "text"]
        return " ".join(p.strip() for p in pieces if p.strip()) or None

    def text(self, system, user, max_tokens, timeout):
        try:
            data = _post_json(self.base_url + "/v1/messages", self._headers(), {
                "model": self.text_model, "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }, timeout)
        except Exception:
            return None
        return self._extract(data)

    def vision(self, system, prompt, png_bytes, max_tokens, timeout):
        b64 = base64.standard_b64encode(png_bytes).decode("ascii")
        try:
            data = _post_json(self.base_url + "/v1/messages", self._headers(), {
                "model": self.vision_model, "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": prompt},
                ]}],
            }, timeout)
        except Exception:
            return None
        return self._extract(data)


class OpenAICompatProvider(Provider):
    """OpenAI Chat Completions - and therefore also LM Studio, vLLM,
    llama.cpp, OpenRouter, Groq, Together, and Ollama's /v1 endpoint."""

    name = "openai"
    DEFAULT_BASE = "https://api.openai.com/v1"
    DEFAULT_TEXT = "gpt-4o-mini"
    DEFAULT_VISION = "gpt-4o-mini"

    def __init__(self, api_key=None, base_url=None, text_model=None,
                 vision_model=None, require_key=True):
        self.api_key = api_key or os.environ.get("WILLOW_API_KEY", "").strip() \
            or os.environ.get("OPENAI_API_KEY", "").strip() \
            or read_key_file("openai_key") or ""
        self.base_url = (base_url or self.DEFAULT_BASE).rstrip("/")
        self.text_model = text_model or self.DEFAULT_TEXT
        self.vision_model = vision_model or self.DEFAULT_VISION
        self.require_key = require_key
        self.label = f"OpenAI-compatible at {self.base_url} ({self.text_model})"

    def available(self):
        return bool(self.api_key) or not self.require_key

    def problem(self):
        if self.require_key and not self.api_key:
            return ("no OpenAI API key. Set OPENAI_API_KEY, or put your key in "
                    + os.path.join(user_config_dir(), "openai_key"))
        return None

    def supports_vision(self):
        return True

    def _headers(self):
        h = {}
        if self.api_key:
            h["Authorization"] = "Bearer " + self.api_key
        return h

    def _token_key(self):
        # Some newer OpenAI models reject `max_tokens` and want
        # `max_completion_tokens`. Choose by model name, and retry on 400.
        m = (self.text_model or "").lower()
        if m.startswith(("o1", "o3", "o4", "gpt-5")):
            return "max_completion_tokens"
        return "max_tokens"

    def _chat(self, messages, model, max_tokens, timeout):
        url = self.base_url + "/chat/completions"
        for key in (self._token_key(), "max_tokens", "max_completion_tokens"):
            payload = {"model": model, "messages": messages, key: max_tokens}
            try:
                data = _post_json(url, self._headers(), payload, timeout)
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    continue          # try the other token parameter name
                return None
            except Exception:
                return None
            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                return None
            if isinstance(text, list):   # some servers return content parts
                text = " ".join(p.get("text", "") for p in text
                                if isinstance(p, dict))
            return (text or "").strip() or None
        return None

    def text(self, system, user, max_tokens, timeout):
        return self._chat([{"role": "system", "content": system},
                           {"role": "user", "content": user}],
                          self.text_model, max_tokens, timeout)

    def vision(self, system, prompt, png_bytes, max_tokens, timeout):
        b64 = base64.standard_b64encode(png_bytes).decode("ascii")
        return self._chat([
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64," + b64}},
            ]},
        ], self.vision_model, max_tokens, timeout)


class OllamaProvider(OpenAICompatProvider):
    """A model running on this machine. No key, no internet, no cost, and
    nothing about your project ever leaves the computer.

    Text goes through Ollama's OpenAI-compatible endpoint; images go through
    its native /api/chat, which is the stable route for them.
    """

    name = "ollama"
    DEFAULT_HOST = "http://localhost:11434"

    # The one to install if you're installing one. Every Gemma 4 variant takes
    # text AND images, so a single pull covers both narration and `describe` -
    # where the older pairing needed a small text model plus a separate vision
    # model, and then made Ollama swap between them.
    RECOMMENDED = "gemma4"
    RECOMMENDED_NOTE = ("gemma4 - one model, about 10 GB, and it handles both "
                        "the narration and describing your app")

    # Model families that can actually look at a picture:
    VISION_HINTS = ("gemma4", "llava", "vision", "moondream", "bakllava",
                    "minicpm-v", "qwen2-vl", "qwen2.5vl", "qwen3-vl", "gemma3",
                    "llama3.2-vision")

    # Preferred text models, best first, only consulted when several are
    # installed. Narration is a small rewording job under a 1.5 second budget,
    # so smaller is genuinely better here - a 31B would just miss the deadline
    # and get replaced by the plain wording anyway.
    TEXT_PREFS = ("gemma4:e2b", "gemma4:e4b", "gemma4:12b", "gemma4",
                  "llama3.2:3b", "llama3.2", "qwen2.5:3b", "phi3", "gemma3:4b",
                  "mistral", "llama3.1:8b", "qwen2.5")

    def __init__(self, base_url=None, text_model=None, vision_model=None):
        host = (base_url or os.environ.get("OLLAMA_HOST") or self.DEFAULT_HOST).rstrip("/")
        if host.endswith("/v1"):
            host = host[:-3]
        if not host.startswith("http"):
            host = "http://" + host
        self.host = host
        super().__init__(api_key="", base_url=host + "/v1",
                         text_model=text_model, vision_model=vision_model,
                         require_key=False)
        self._installed = None
        self._unreachable = False
        self._auto_text = text_model
        self._auto_vision = vision_model
        self.label = f"Ollama at {self.host}"

    # ---- what is actually installed on this machine ----------------------

    def installed(self, timeout=1.5):
        """Model names, or None if Ollama could not be reached at all.

        None and [] mean different things and get different advice: one is
        "install Ollama", the other is "you have Ollama, pull a model".
        """
        if self._installed is None and not self._unreachable:
            try:
                data = _get_json(self.host + "/api/tags", timeout)
                self._installed = [m.get("name", "") for m in data.get("models", [])]
            except Exception:
                self._unreachable = True
        return self._installed

    def reachable(self):
        return self.installed() is not None

    def available(self):
        return bool(self.installed())

    def problem(self):
        if not self.reachable():
            return (f"Ollama isn't answering at {self.host}. Install it from "
                    f"ollama.com, then run: ollama pull {self.RECOMMENDED}")
        if not self.pick_text():
            return ("Ollama is running, but no models are installed yet. Run: "
                    "ollama pull " + self.RECOMMENDED)
        return None

    def pick_text(self):
        """Use the configured model, else the best small one installed."""
        if self._auto_text:
            return self._auto_text
        names = self.installed() or []
        if not names:
            return None
        for pref in self.TEXT_PREFS:
            for n in names:
                if n.startswith(pref):
                    return n
        return names[0]

    def pick_vision(self):
        if self._auto_vision:
            return self._auto_vision
        installed = self.installed() or []

        # If the model already chosen for narration can also look at pictures,
        # use it. Ollama keeps one model resident at a time, so reusing it
        # avoids evicting and reloading several gigabytes every time someone
        # types `describe`.
        text = self.pick_text()
        if text and any(h in text.lower() for h in self.VISION_HINTS):
            return text

        for pref in self.VISION_HINTS:
            for n in installed:
                if pref in n.lower():
                    return n
        return None

    def supports_vision(self):
        return self.pick_vision() is not None

    def vision_problem(self):
        if self.pick_vision():
            return None
        return ("none of your installed Ollama models can look at a picture. "
                "Run: ollama pull " + self.RECOMMENDED + " - that one does "
                "both the narration and this.")

    # ---- requests --------------------------------------------------------

    def warm_up(self):
        """Ollama loads the model from disk on first use, which can take
        seconds. Pay that cost at startup instead of mid-announcement."""
        model = self.pick_text()
        if not model:
            return
        try:
            _post_json(self.host + "/api/generate", {},
                       {"model": model, "prompt": "hi", "stream": False,
                        "options": {"num_predict": 1}}, 60)
        except Exception:
            pass

    def text(self, system, user, max_tokens, timeout):
        model = self.pick_text()
        if not model:
            return None
        self.text_model = model
        return self._chat([{"role": "system", "content": system},
                           {"role": "user", "content": user}],
                          model, max_tokens, timeout)

    def vision(self, system, prompt, png_bytes, max_tokens, timeout):
        model = self.pick_vision()
        if not model:
            return None
        b64 = base64.standard_b64encode(png_bytes).decode("ascii")
        try:
            data = _post_json(self.host + "/api/chat", {}, {
                "model": model, "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt, "images": [b64]},
                ],
                "options": {"num_predict": max_tokens},
            }, timeout)
        except Exception:
            return None
        text = (data.get("message") or {}).get("content", "")
        return (text or "").strip() or None


def resolve_provider(cfg):
    """Pick a backend. Explicit choice wins; otherwise prefer a key that is
    already set, and fall back to a local model if one is running."""
    choice = (cfg.get("provider") or "auto").lower()
    base, tm, vm = cfg.get("base_url"), cfg.get("text_model"), cfg.get("vision_model")

    if choice == "none":
        return Provider()
    if choice == "anthropic":
        return AnthropicProvider(base_url=base, text_model=tm, vision_model=vm)
    if choice == "openai":
        return OpenAICompatProvider(base_url=base, text_model=tm, vision_model=vm)
    if choice == "ollama":
        return OllamaProvider(base_url=base, text_model=tm, vision_model=vm)
    if choice in ("openai-compat", "compat", "local"):
        return OpenAICompatProvider(base_url=base, text_model=tm,
                                    vision_model=vm, require_key=False)

    for candidate in (AnthropicProvider(base_url=base, text_model=tm, vision_model=vm),
                      OpenAICompatProvider(base_url=base, text_model=tm, vision_model=vm),
                      OllamaProvider(base_url=base, text_model=tm, vision_model=vm)):
        if candidate.available():
            return candidate
    return Provider()


# ==========================================================================
# The faithfulness check - what makes ANY model safe to use here
# ==========================================================================
#
# Willow's safety claim for narration is that the model only ever REWORDS
# facts Willow already checked. Against a large model, an instruction saying so
# mostly holds. Against a small local model - or any endpoint a user points
# `base_url` at - it is enforced by nothing at all.
#
# So don't rely on the instruction: check the answer. A narration may only
# contain numbers, filenames and verdicts that appear in the verified facts.
# Anything else is discarded and the plain factual line is spoken instead.
# That turns "the narrator cannot invent a pass/fail" from a hope about a
# prompt into an invariant with a test.

_NUM = re.compile(r"\d+")
_PATH = re.compile(r"[\w./\\-]*[\w-]\.[A-Za-z0-9]{1,8}\b")
_PASS_WORDS = re.compile(r"\b(passed|passes|passing|succeeded|succeeds|success|green|worked)\b", re.I)
_FAIL_WORDS = re.compile(r"\b(failed|fails|failing|failure|broke|broken|red|errored)\b", re.I)

_WORD_NUMBERS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100",
}


def _numbers(text):
    """Digits in the text, plus small numbers written as words."""
    found = set(_NUM.findall(text))
    for word, digit in _WORD_NUMBERS.items():
        if re.search(r"\b" + word + r"\b", text, re.I):
            found.add(digit)
    return found


def _verdicts(text):
    out = set()
    if _PASS_WORDS.search(text):
        out.add("pass")
    if _FAIL_WORDS.search(text):
        out.add("fail")
    return out


def faithful(facts, spoken):
    """True if `spoken` says nothing `facts` did not already establish.

    Deliberately one-directional: leaving something out is fine, adding
    something is not.
    """
    if not spoken or not spoken.strip():
        return False
    if not _numbers(spoken) <= _numbers(facts):
        return False
    if not set(_PATH.findall(spoken)) <= set(_PATH.findall(facts)):
        return False
    if not _verdicts(spoken) <= _verdicts(facts):
        return False
    return True


# ==========================================================================
# Making text speakable (without changing the facts that get logged)
# ==========================================================================

def speakable(text):
    """Small adjustments so a synthesiser reads a factual line correctly.

    Only the SPOKEN form changes; the exact factual line is what gets printed
    and logged. Keeping this here - rather than asking the model to do it -
    is what lets the faithfulness check above stay strict about filenames.
    """
    t = " ".join(text.split())
    t = re.sub(r"\(\+(\d+) -(\d+) lines\)", r"(plus \1, minus \2 lines)", t)
    t = re.sub(r"\(\+(\d+) lines\)", r"(plus \1 lines)", t)
    t = re.sub(r"\(-(\d+) lines\)", r"(minus \1 lines)", t)
    t = t.replace("\\", "/")
    t = re.sub(r"\bexit code (\d+)", r"exit code \1", t)
    return t


# ==========================================================================
# Speaking out loud
# ==========================================================================

P_URGENT = 0      # failures, reverts, stuck, errors, things you asked for
P_NORMAL = 1      # routine file changes

MAX_PENDING_NORMAL = 4   # beyond this, drop the oldest routine announcements


class Speaker:
    """Speaks announcements aloud, in its own thread.

    Three things the first version got wrong, fixed here:
      * On Windows it spawned a whole PowerShell per sentence (~0.9 s of dead
        air each). Now one host process is started once and fed over stdin.
      * Text was interpolated into a command line, so any non-ASCII filename
        was mangled before the voice ever saw it. Now it is base64 UTF-8, so
        it cannot be damaged in transit.
      * There was no way to make it stop, and no priority - a failure queued
        up behind a list of edited files. Now failures jump the queue and
        `stop` clears it.
    """

    def __init__(self, enabled=True, rate=3, narrator=None, on_spoken=None,
                 use_host=True):
        self.enabled = enabled
        self.rate = int(rate)
        self.use_host = use_host
        self.narrator = narrator      # optional: rewords facts more naturally
        self.on_spoken = on_spoken    # called with (facts, spoken) for the log
        self.system = platform.system()
        self.q = queue.PriorityQueue()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._generation = 0          # bumped by stop(); older items are dropped
        self._host = None
        self._host_broken = False
        self._child = None            # currently running one-shot say/espeak
        self._warned = False
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # ---- public ----------------------------------------------------------

    def say(self, text, priority=P_NORMAL, narratable=False):
        if not self.enabled:
            if self.on_spoken:
                self.on_spoken(text, text)
            return
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        self._drop_excess()
        self.q.put((priority, seq, self._generation, text, narratable))

    def stop_talking(self):
        """Shut up right now and forget what was queued."""
        self._generation += 1
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        self._cancel_current()

    def set_rate(self, rate):
        self.rate = max(-10, min(10, int(rate)))
        if self._host is not None:
            self._send("R%d" % self.rate)
        return self.rate

    def close(self):
        self._stop.set()
        self._cancel_current()
        if self._host is not None:
            try:
                self._host.stdin.close()
            except OSError:
                pass

    # ---- internals -------------------------------------------------------

    def _drop_excess(self):
        """Keep the backlog of routine announcements short. Being three
        announcements behind is worse than missing one."""
        pending = []
        try:
            while True:
                pending.append(self.q.get_nowait())
        except queue.Empty:
            pass
        normal = [p for p in pending if p[0] != P_URGENT]
        if len(normal) > MAX_PENDING_NORMAL:
            drop = set(id(x) for x in normal[:len(normal) - MAX_PENDING_NORMAL])
            pending = [p for p in pending if id(p) not in drop]
        for p in pending:
            self.q.put(p)

    def _run(self):
        while not self._stop.is_set():
            try:
                priority, _seq, gen, text, narratable = self.q.get(timeout=0.25)
            except queue.Empty:
                continue
            if gen != self._generation:
                continue              # superseded by `stop`
            spoken = text
            if narratable and self.narrator is not None:
                alt = self.narrator.narrate(text)
                if alt:
                    spoken = alt
            if self.on_spoken:
                try:
                    self.on_spoken(text, spoken)
                except Exception:
                    pass
            if gen != self._generation:
                continue
            try:
                self._speak_blocking(speakable(spoken))
            except Exception:
                if not self._warned:
                    self._warned = True
                    print("Willow: (note) I couldn't use the voice on this "
                          "computer; text updates will still appear.", flush=True)

    def _cancel_current(self):
        if self._host is not None:
            self._send("X")
        child = self._child
        if child is not None and child.poll() is None:
            try:
                child.kill()
            except OSError:
                pass

    def _send(self, line):
        try:
            self._host.stdin.write(line + "\n")
            self._host.stdin.flush()
            return True
        except (OSError, ValueError, AttributeError):
            self._host = None
            self._host_broken = True
            return False

    # ---- Windows: one long-lived voice host ------------------------------

    # Notes on this script, all learned the hard way on Windows:
    #
    #  * It is handed over with -EncodedCommand, never -Command. A multi-line
    #    script passed to -Command alongside a piped stdin takes the parent
    #    Python process down with it.
    #  * It does NOT touch [Console]::InputEncoding. Setting that throws when
    #    stdin is a pipe rather than a console, which killed the host. It isn't
    #    needed: every line on the wire is plain ASCII base64, and the UTF-8
    #    decoding happens here, inside PowerShell. That is also why a filename
    #    in any language arrives intact.
    #  * It says WILLOW-READY before doing anything else, and Willow will not
    #    use it until it has heard that. Speech that silently goes nowhere is
    #    the worst outcome for a user who cannot see the screen, so the host is
    #    treated as guilty until it proves itself, and Willow drops back to
    #    starting a process per sentence - slower, but known to work.
    HOST_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = %RATE%
[Console]::Out.WriteLine('WILLOW-READY')
[Console]::Out.Flush()
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($null -eq $line) { break }
  if ($line.Length -lt 1) { continue }
  try {
    $cmd = $line.Substring(0,1)
    $arg = $line.Substring(1)
    if ($cmd -eq 'X') { $s.SpeakAsyncCancelAll() }
    elseif ($cmd -eq 'R') { $s.Rate = [int]$arg }
    elseif ($cmd -eq 'S') {
      $text = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($arg))
      $s.SpeakAsync($text) | Out-Null
    }
  } catch { }
}
"""
    HOST_READY_TIMEOUT = 4.0

    def _ensure_host(self):
        if self._host is not None:
            if self._host.poll() is None:
                return True
            self._host = None            # it died on us; never trust it again
            self._host_broken = True
        if self._host_broken or not self.use_host:
            return False
        script = self.HOST_SCRIPT.replace("%RATE%", str(self.rate))
        encoded = base64.standard_b64encode(
            script.encode("utf-16-le")).decode("ascii")
        for shell in ("powershell", "pwsh"):
            try:
                proc = subprocess.Popen(
                    [shell, "-NoProfile", "-EncodedCommand", encoded],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding="ascii",
                    errors="replace")
            except OSError:
                continue
            if self._wait_for_ready(proc):
                self._host = proc
                return True
            try:
                proc.kill()
            except OSError:
                pass
        self._host_broken = True
        return False

    def _wait_for_ready(self, proc):
        """Read one line, with a timeout, without hanging the speaker thread."""
        got = []

        def reader():
            try:
                got.append(proc.stdout.readline())
            except Exception:
                got.append("")

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(self.HOST_READY_TIMEOUT)
        return bool(got) and "WILLOW-READY" in (got[0] or "")

    def _speak_blocking(self, text):
        one_line = " ".join(text.split())
        if not one_line:
            return
        if self.system == "Windows":
            if self._ensure_host():
                payload = base64.standard_b64encode(
                    one_line.encode("utf-8")).decode("ascii")
                # Sending is not enough: a host that died between the check and
                # the write would swallow the sentence in silence. Confirm it
                # is still alive afterwards, and if not, say it again properly.
                if self._send("S" + payload):
                    time.sleep(0.05)
                    if self._host is not None and self._host.poll() is None:
                        return
                    self._host, self._host_broken = None, True
            self._speak_windows_oneshot(one_line)
        elif self.system == "Darwin":
            # macOS `say` starts fast, so one process per line is fine; killing
            # it is also how `stop` works there.
            words_per_min = int(175 * (1.12 ** self.rate))
            self._child = subprocess.Popen(
                ["say", "-r", str(max(90, min(700, words_per_min))), "--", one_line],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._child.wait()
        else:
            for argv in (["spd-say", "-w", "-r", str(self.rate * 10), one_line],
                         ["espeak", "-s", str(int(160 * (1.1 ** self.rate))), one_line]):
                try:
                    self._child = subprocess.Popen(
                        argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self._child.wait()
                    return
                except FileNotFoundError:
                    continue
            raise RuntimeError("no speech engine found (tried spd-say, espeak)")

    def _speak_windows_oneshot(self, one_line):
        """Fallback when the long-lived host isn't usable.

        The sentence travels in an environment variable, NOT on the command
        line. Two reasons, both real:

          * Windows environment blocks are Unicode, so a filename in any
            language arrives exactly as written - which is the whole point.
            Interpolating it into the command line instead is what used to
            turn 'テスト.py' into '???.py'.
          * The obvious alternative, base64 on the command line, is the
            classic obfuscated-malware shape. AMSI blocks it on a normally
            configured Windows machine: PowerShell dies instantly with no
            error, and the user hears nothing at all.
        """
        script = ("Add-Type -AssemblyName System.Speech;"
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  f"$s.Rate = {self.rate};"
                  "$s.Speak($env:WILLOW_SPEAK_TEXT)")
        env = dict(os.environ, WILLOW_SPEAK_TEXT=one_line)
        self._child = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        self._child.wait()


# ==========================================================================
# Optional: a more natural voice (a WORDING layer, never a decision layer)
# ==========================================================================

class Narrator:
    """Rewords Willow's own verified facts into a natural spoken sentence.

    It is handed the finished factual line and nothing else - never your code,
    never the agent's account - so it cannot introduce a fact. And because
    `faithful()` checks the result before anything is spoken, that is enforced
    rather than merely requested. Any failure, refusal, slow reply or unfaithful
    answer falls back silently to the plain factual sentence.
    """

    SYSTEM = (
        "You are the voice of Willow, a tool that tells a blind developer what "
        "an AI coding agent just did to their project. You will be given a line "
        "of VERIFIED FACTS that Willow checked for itself. Rephrase it into one "
        "or two short, natural spoken sentences for a screen-reader user. "
        "Rules: say ONLY what the facts state. Do not add, guess, soften, or "
        "invent anything - in particular, never say a build or test passed or "
        "failed unless the facts say so. Copy file names and numbers EXACTLY as "
        "written, character for character - do not spell them out or adjust "
        "them. No preamble and no sign-off - just the spoken sentence."
    )
    MAX_TOKENS = 200

    def __init__(self, provider, timeout=1.5, on_reject=None):
        self.provider = provider
        self.timeout = timeout
        self.on_reject = on_reject
        self.enabled = provider is not None and provider.available()
        self._warned = False
        self._cache = {}
        self.rejected = 0
        self.accepted = 0

    def narrate(self, facts):
        """A natural rewording of `facts`, or None to use the plain wording."""
        if not self.enabled:
            return None
        if facts in self._cache:
            return self._cache[facts]
        text = self.provider.text(self.SYSTEM, facts, self.MAX_TOKENS, self.timeout)
        if not text:
            if not self._warned:
                self._warned = True
                print("Willow: (note) natural narration is unavailable right "
                      "now; using plain wording. I won't mention this again.",
                      flush=True)
            return None
        text = " ".join(text.split())
        if not faithful(facts, text):
            # The model said something the facts do not support. Throw it away.
            self.rejected += 1
            if self.on_reject:
                self.on_reject(facts, text)
            return None
        self.accepted += 1
        if len(self._cache) < 500:
            self._cache[facts] = text
        return text


# ==========================================================================
# Optional: describing what the app LOOKS like (the "guess" channel)
# ==========================================================================

class Viewer:
    """Describes how your app looks, on demand ('describe' while watching).

    A screen description is unavoidably an interpretation, so it is kept apart
    from the facts: Willow announces the window it captured (its real title - a
    FACT) before the description, so a wrong grab is obvious the moment you
    hear it, and the description itself is labelled a guess. If it can't find
    the window, or it's minimised, or no model can see pictures, it says so.
    """

    SYSTEM = (
        "You are describing a screenshot of an app to a blind developer who is "
        "building it and cannot see the screen. Describe what is visibly on "
        "screen in plain, spoken language: the overall layout, the main "
        "headings and text, buttons, links, form fields and their labels, "
        "colours, and how things are arranged. Be concrete and specific about "
        "what you can see. This is a description of APPEARANCE only - an "
        "interpretation, not a verified fact. Do not claim anything works, "
        "passed, or failed, and do not guess about code or behaviour. Keep it "
        "to one clear paragraph. No preamble."
    )
    MAX_TOKENS = 500

    def __init__(self, provider, timeout=30):
        self.provider = provider
        self.timeout = timeout
        self._skip = 0          # how many candidate windows to pass over

    def next_window(self):
        self._skip += 1

    def reset_window(self):
        self._skip = 0

    def describe(self, hint, out_path, keep=False):
        """A list of announcement lines: the fact first, then the guess."""
        if self.provider is None or not self.provider.available():
            return ["To describe your app I need a model that can look at "
                    "pictures. Run willow doctor to see the options - a local "
                    "one through Ollama works and needs no key."]
        if not self.provider.supports_vision():
            reason = getattr(self.provider, "vision_problem", lambda: None)()
            return [reason or "The model Willow is set up with can't look at "
                              "pictures."]
        if not IS_WINDOWS:
            return ["Describing your app isn't set up for this operating "
                    "system yet - right now it works on Windows."]

        status, name, title = self._capture_windows(hint, out_path)
        if status == "NOWINDOW":
            self.reset_window()
            return ["I couldn't find a browser window showing your app. Make "
                    "sure your app is open in a browser, then type describe "
                    "again."]
        if status == "MINIMIZED":
            return [f"Your app window '{title}' seems to be minimised, so I "
                    "can't see it right now. Bring it up and try describe "
                    "again."]
        if status != "OK":
            return ["I couldn't capture your app window just now. Please try "
                    "describe again in a moment."]

        pretty = {"chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
                  "brave": "Brave", "opera": "Opera", "vivaldi": "Vivaldi",
                  "arc": "Arc", "chromium": "Chromium"}.get(
                      (name or "").lower(), name or "a browser")
        fact = (f"Describing your app window: {pretty} - '{title}'. "
                "If that's the wrong window, type other.")

        try:
            with open(out_path, "rb") as f:
                png = f.read()
        except OSError:
            return [fact, "I found your app window but couldn't read the "
                          "picture back. Please try describe again."]
        finally:
            if not keep:
                try:
                    os.remove(out_path)
                except OSError:
                    pass

        desc = self.provider.vision(self.SYSTEM, "Describe what is on this "
                                    "screen.", png, self.MAX_TOKENS, self.timeout)
        if not desc:
            return [fact, "I found your app window but couldn't get a "
                          "description just now. Please try describe again."]
        guess = ("Here's what it looks like - this is my interpretation, not a "
                 "checked fact. " + " ".join(desc.split()))
        return [fact, guess]

    # ---- finding + photographing the app's browser window (Windows) -------

    def _capture_windows(self, hint, out_path):
        port = self._detect_dev_port()
        script = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class WCap {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint nFlags);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
"@
$browsers = @(%BROWSERS%)
$hint = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%HINT%'))
$port = '%PORT%'
$wins = @(Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -ne '' -and ($browsers -contains $_.ProcessName.ToLower()) })
if ($wins.Count -eq 0) { Write-Output 'NOWINDOW'; exit }
$ranked = @()
if ($port -ne '') { $ranked += @($wins | Where-Object { $_.MainWindowTitle -like "*$port*" }) }
if ($hint -ne '') { $ranked += @($wins | Where-Object { $_.MainWindowTitle -like "*$hint*" }) }
$ranked += $wins
$seen = @{}; $ordered = @()
foreach ($w in $ranked) { if (-not $seen.ContainsKey($w.Id)) { $seen[$w.Id] = $true; $ordered += $w } }
$skip = [int]'%SKIP%'
if ($skip -ge $ordered.Count) { $skip = 0 }
$target = $ordered[$skip]
$h = $target.MainWindowHandle
$title = $target.MainWindowTitle
$name = $target.ProcessName
if ([WCap]::IsIconic($h)) { Write-Output ("MINIMIZED`t" + $title); exit }
$r = New-Object WCap+RECT
[WCap]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.Right - $r.Left
$hh = $r.Bottom - $r.Top
if ($w -le 0 -or $hh -le 0) { Write-Output 'ERROR'; exit }
$bmp = New-Object System.Drawing.Bitmap $w, $hh
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
[WCap]::PrintWindow($h, $hdc, 2) | Out-Null
$g.ReleaseHdc($hdc)
$g.Dispose()
$final = $bmp
$maxw = %MAXW%
if ($w -gt $maxw) {
  $nh = [int]($hh * $maxw / $w)
  $scaled = New-Object System.Drawing.Bitmap $maxw, $nh
  $sg = [System.Drawing.Graphics]::FromImage($scaled)
  $sg.DrawImage($bmp, 0, 0, $maxw, $nh)
  $sg.Dispose()
  $final = $scaled
}
$out = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%OUT%'))
$final.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$final.Dispose()
$bmp.Dispose()
Write-Output ("OK`t" + $name + "`t" + $title)
'''
        b64 = lambda s: base64.standard_b64encode(s.encode("utf-8")).decode("ascii")
        script = (script
                  .replace("%BROWSERS%", ",".join("'%s'" % b for b in VIEW_BROWSERS))
                  .replace("%HINT%", b64(hint or ""))
                  .replace("%PORT%", str(port or ""))
                  .replace("%SKIP%", str(self._skip))
                  .replace("%MAXW%", str(VIEW_MAX_WIDTH))
                  .replace("%OUT%", b64(out_path)))
        # -EncodedCommand rather than -Command: a multi-line script on the
        # command line is fragile on Windows (see Speaker.HOST_SCRIPT).
        encoded = base64.standard_b64encode(
            script.encode("utf-16-le")).decode("ascii")
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-EncodedCommand", encoded],
                capture_output=True, text=True, timeout=self.timeout,
                encoding="utf-8", errors="replace")
            lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        except Exception:
            return ("ERROR", None, None)
        if not lines:
            return ("ERROR", None, None)
        fields = lines[-1].split("\t")
        if fields[0] == "OK" and len(fields) >= 3:
            return ("OK", fields[1], fields[2])
        if fields[0] == "MINIMIZED" and len(fields) >= 2:
            return ("MINIMIZED", None, fields[1])
        return (fields[0], None, None)

    def _detect_dev_port(self):
        """Best-effort: a single obvious local dev server port, else None."""
        common = {3000, 3001, 4000, 4200, 5000, 5173, 5174,
                  8000, 8080, 8081, 9000, 1234, 3333}
        try:
            proc = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                  capture_output=True, text=True, timeout=5)
        except Exception:
            return None
        found = set()
        for line in proc.stdout.splitlines():
            if "LISTENING" not in line:
                continue
            parts = line.split()
            if len(parts) < 2 or ":" not in parts[1]:
                continue
            try:
                port = int(parts[1].rsplit(":", 1)[1])
            except ValueError:
                continue
            if port in common:
                found.add(port)
        return next(iter(found)) if len(found) == 1 else None


# ==========================================================================
# Looking at the folder (Willow's "eyes")
# ==========================================================================

def is_ignored(relpath, ignore_dirs=None):
    ignore_dirs = ignore_dirs or IGNORE_DIRS
    parts = relpath.replace("\\", "/").split("/")
    for p in parts[:-1]:
        if p in ignore_dirs:
            return True
    name = parts[-1]
    if name in IGNORE_FILE_NAMES:
        return True
    if name.endswith(IGNORE_FILE_SUFFIXES):
        return True
    return False


def git_ignored_dirs(folder):
    """Top-level folder names git already ignores (build output and the like).

    One cheap call at startup, so Willow doesn't narrate a build directory.
    Silently does nothing outside a git repository.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", folder, "ls-files", "--others", "--ignored",
             "--exclude-standard", "--directory"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode != 0:
        return set()
    names = set()
    for line in proc.stdout.splitlines():
        line = line.strip().rstrip("/")
        if line and "/" not in line and not line.startswith("."):
            names.add(line)
    return names


class Eyes:
    """Two-phase folder watching.

    The first version read and hashed EVERY file EVERY second, which cost 65%
    of a core on an 80 MB project and held four full copies of the text in
    memory. Here the per-tick pass only reads directory entries (size + mtime,
    which `scandir` returns for free) and hashes just the files that moved.

    It also never drops a file it merely failed to read. A file locked for a
    moment by an editor or a virus scanner used to vanish from the snapshot and
    come back, which Willow reported as a silent undo that never happened.
    """

    def __init__(self, folder, ignore_dirs=None):
        self.folder = folder
        self.ignore_dirs = set(IGNORE_DIRS) | set(ignore_dirs or ())
        self.meta = {}       # rel -> (size, mtime_ns)
        self.hashes = {}     # rel -> sha1 hex
        self.text = {}       # rel -> decoded text (only where we keep it)
        self.text_bytes = 0
        self.recent = {}     # rel -> ticks remaining of forced re-hashing
        self.unreadable = set()
        self._last_look_ns = time.time_ns()

    # ---- phase one: directory entries only (cheap) -----------------------

    def _walk_meta(self):
        meta, stack = {}, [self.folder]
        while stack:
            cur = stack.pop()
            try:
                entries = list(os.scandir(cur))
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.name not in self.ignore_dirs:
                            stack.append(e.path)
                        continue
                except OSError:
                    continue
                rel = os.path.relpath(e.path, self.folder)
                if is_ignored(rel, self.ignore_dirs):
                    continue
                try:
                    st = e.stat()
                    meta[rel] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    # Can't stat it right now. Keep whatever we knew before
                    # rather than pretending it was deleted.
                    if rel in self.meta:
                        meta[rel] = self.meta[rel]
        return meta

    # ---- phase two: read + hash only what moved --------------------------

    def _read(self, rel, keep_text):
        full = os.path.join(self.folder, rel)
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            return None, None
        digest = hashlib.sha1(data).hexdigest()
        text = None
        if keep_text and len(data) <= MAX_DIFF_BYTES:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None       # binary
        return digest, text

    def prime(self):
        """One full read at startup, to have something to diff against.

        Reading everything once costs about a second on a large project. It was
        doing it every second that was the problem.
        """
        self.meta = self._walk_meta()
        for rel in self.meta:
            keep = self.text_bytes < MAX_BASELINE_BYTES
            digest, text = self._read(rel, keep)
            if digest is None:
                continue
            self.hashes[rel] = digest
            if text is not None:
                self.text[rel] = text
                self.text_bytes += len(text)
        self._last_look_ns = time.time_ns()

    # Filesystem timestamps are coarser than they look (Windows updates the
    # clock about every 15 ms), so two writes close together that end at the
    # same size can share a size AND an mtime. Anything modified since roughly
    # the previous look is re-hashed regardless, which closes that window.
    CLOCK_SLACK_NS = 30_000_000

    def look(self):
        """Return (changed_paths, deleted_paths) since the last look."""
        look_started_ns = time.time_ns()
        new_meta = self._walk_meta()
        candidates = set()

        for rel, m in new_meta.items():
            if rel not in self.meta or self.meta[rel] != m:
                candidates.add(rel)
            elif m[1] >= self._last_look_ns - self.CLOCK_SLACK_NS:
                candidates.add(rel)
        # Anything that moved in the last few ticks is re-checked too.
        candidates |= {r for r in self.recent if r in new_meta}

        gone = []
        for rel in self.meta:
            if rel in new_meta:
                continue
            if os.path.lexists(os.path.join(self.folder, rel)):
                new_meta[rel] = self.meta[rel]   # unreadable, not deleted
                continue
            gone.append(rel)

        changed = []
        for rel in candidates:
            keep = rel in self.text or self.text_bytes < MAX_BASELINE_BYTES
            digest, text = self._read(rel, keep)
            if digest is None:
                new_meta[rel] = self.meta.get(rel, new_meta.get(rel))
                continue
            if self.hashes.get(rel) != digest:
                changed.append(rel)
                self.hashes[rel] = digest
                if text is not None:
                    self.text_bytes += len(text) - len(self.text.get(rel, ""))
                    self.text[rel] = text
                else:
                    self.text.pop(rel, None)
                self.recent[rel] = 3

        for rel in gone:
            self.hashes.pop(rel, None)
            self.text_bytes -= len(self.text.pop(rel, ""))

        self.recent = {r: n - 1 for r, n in self.recent.items() if n > 1}
        self.meta = new_meta
        self._last_look_ns = look_started_ns
        return changed, gone

    # ---- what the rulebook asks about ------------------------------------

    def snapshot_hashes(self):
        return dict(self.hashes)

    def current_text(self, rel):
        return self.text.get(rel)


def line_delta(old_text, new_text):
    """(added_lines, removed_lines) between two text blobs."""
    old_lines = old_text.splitlines() if old_text else []
    new_lines = new_text.splitlines() if new_text else []
    added = removed = 0
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def describe_change(rel, existed_before, exists_now, before_text, after_text):
    """One short human phrase describing the net change to one file."""
    if not existed_before and exists_now:
        if after_text is not None:
            n = len(after_text.splitlines())
            return f"created {rel} ({n} lines)" if n else f"created {rel}"
        return f"created {rel}"
    if existed_before and not exists_now:
        return f"deleted {rel}"
    if before_text is not None and after_text is not None:
        added, removed = line_delta(before_text, after_text)
        bits = []
        if added:
            bits.append(f"+{added}")
        if removed:
            bits.append(f"-{removed}")
        if not bits:
            return f"edited {rel} (same lines, different bytes)"
        return f"edited {rel} ({' '.join(bits)} lines)"
    if after_text is not None:
        return f"changed {rel} (now {len(after_text.splitlines())} lines)"
    return f"changed {rel}"


# ==========================================================================
# Reading build/test results
# ==========================================================================

def parse_result_line(line):
    """One line of the results tray -> a check, or None if unreadable.

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


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Each entry: (pattern that identifies the runner, how to build the summary).
# Everything these produce is quoted from the command's own output, so it is
# a fact by exactly the same standard as the exit code.
_SUMMARY_RULES = [
    (re.compile(r"^=+ .*?(\d+) failed.*?=+$", re.M),
     re.compile(r"^(FAILED [^\s]+.*)$", re.M)),
]


def summarize_output(text, exit_code):
    """A short, factual account of WHY a command failed, quoted from its output.

    'exit code 1' is the least useful thing Willow can say at the moment the
    user most needs help, and finding the reason means hunting scrollback with
    a screen reader. This quotes the runner's own summary instead.
    """
    if not text:
        return None
    text = _ANSI.sub("", text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    joined = "\n".join(lines)

    # pytest. The tail summary keeps its ==== decoration in normal mode and
    # loses it under -q, so both forms have to be recognised.
    m = re.search(r"^=*\s*(\d+ (?:failed|passed|error|errors|skipped|xfailed|"
                  r"xpassed|deselected)[^=]*?)\s*=*$", joined, re.M)
    if m:
        summary = m.group(1).strip()
        first = re.search(r"^(?:FAILED|ERROR) (\S+)", joined, re.M)
        if first and exit_code != 0:
            return f"{summary}. First failure: {first.group(1)}"
        return summary

    # jest / vitest
    m = re.search(r"^\s*Tests:\s+(.+)$", joined, re.M)
    if m:
        summary = "Tests: " + m.group(1).strip()
        first = re.search(r"^\s*[●x✕]\s+(.+)$", joined, re.M)
        if first and exit_code != 0:
            return f"{summary}. First failure: {first.group(1).strip()}"
        return summary

    # go test
    m = re.search(r"^--- FAIL: (\S+)", joined, re.M)
    if m:
        return f"First failure: {m.group(1)}"

    # cargo / rustc
    m = re.search(r"^error(\[[^\]]+\])?: (.+)$", joined, re.M)
    if m:
        return f"error: {m.group(2).strip()[:140]}"

    # tsc, gcc, clang, msvc: path(line,col): error CODE: message
    m = re.search(r"^(\S+[(:]\d+(?:[,:]\d+)?\)?):\s*(?:fatal )?(error[^:]*):\s*(.+)$",
                  joined, re.M)
    if m:
        return f"{m.group(1)}: {m.group(2).strip()}: {m.group(3).strip()[:120]}"

    # npm. Its most useful line is rarely the last one - the last one is
    # always the path of a log file the user then has to go and open.
    npm = [re.sub(r"^npm (?:ERR!|error)\s*", "", ln).strip()
           for ln in lines if re.match(r"^npm (?:ERR!|error)\b", ln)]
    npm = [n for n in npm if n and not n.startswith((
        "A complete log", "path ", "syscall ", "errno ", "code ", "enoent\n"))
        and n not in ("enoent", "!")]
    if npm:
        best = next((n for n in npm if len(n) > 25), npm[0])
        return f"npm error: {best[:140]}"

    if exit_code != 0:
        for ln in reversed(lines):
            s = ln.strip()
            if s and not s.startswith("  "):
                return s[:140]
    return None


def normalize_exit_code(code):
    """Windows reports a negative exit status as a huge unsigned number.

    'exit code 4294963238' is not a useful thing to say out loud; the same
    fact is '-4058'.
    """
    try:
        code = int(code)
    except (TypeError, ValueError):
        return code
    if code > 2 ** 31:
        code -= 2 ** 32
    return code


# ==========================================================================
# The watcher (Willow's brain) - groups changes into chunks and announces
# ==========================================================================

class Willow:
    def __init__(self, folder, speaker, cfg, viewer=None):
        self.folder = os.path.abspath(folder)
        self.speaker = speaker
        self.cfg = cfg
        self.viewer = viewer
        self.quiet = cfg.get("verbosity") == "quiet"

        wd = os.path.join(self.folder, WILLOW_DIR)
        os.makedirs(wd, exist_ok=True)
        self.log_path = os.path.join(wd, LOG_NAME)
        self.events_path = os.path.join(wd, EVENTS_NAME)
        self.inbox_path = os.path.join(wd, INBOX_NAME)
        self.results_path = os.path.join(wd, RESULTS_NAME)
        self.watching_path = os.path.join(wd, WATCHING_NAME)
        self._log_lock = threading.Lock()
        self._tails = {}     # path -> (offset, file-identity)

        self.eyes = Eyes(self.folder, git_ignored_dirs(self.folder)
                         | set(cfg.get("extra_ignore_dirs") or ()))
        self.eyes.prime()
        self.session_baseline = self.eyes.snapshot_hashes()
        self.baseline_text = dict(self.eyes.text)
        self.committed = dict(self.session_baseline)
        self.committed_text = dict(self.eyes.text)

        self.ep_touched = {}     # rel -> ticks in which this file changed
        self.ep_writes = {}      # rel -> EXACT writes, when a hook reports them
        self.ep_exists_before = {}   # rel -> did it exist when the chunk began
        self.last_change_at = None

        self.fail_streak = {}    # (command, rel) -> consecutive failing chunks
        self.stuck_announced = set()
        self.last_episode = None
        self.session = {"episodes": 0, "checks": 0, "failures": 0,
                        "files": set(), "started": time.time()}
        self.stop = threading.Event()

    # ---- output ----------------------------------------------------------

    def announce(self, text, urgent=False, narratable=False):
        """`text` is ALWAYS the exact verified factual line.

        It is printed and logged immediately. Speaking (and, with narration on,
        rewording) happens on the speaker thread, so a slow model can never
        stall the watching or shift a chunk boundary.
        """
        print("Willow: " + text, flush=True)
        self._write_log(text)
        priority = P_URGENT if urgent else P_NORMAL
        speak = not (self.quiet and not urgent)
        if speak:
            self.speaker.say(text, priority=priority, narratable=narratable)

    def announce_lines(self, header, details, urgent=False, narratable=False):
        """Speak the collapsed form, but print one detail per line so a screen
        reader can arrow through them."""
        print("Willow: " + header, flush=True)
        if len(details) > 1:
            for d in details:
                print("        - " + d, flush=True)
        self._write_log(header)
        if not (self.quiet and not urgent):
            self.speaker.say(header, priority=P_URGENT if urgent else P_NORMAL,
                             narratable=narratable)

    def on_spoken(self, facts, spoken):
        if spoken != facts:
            self._write_log("(spoken) " + spoken)

    def _write_log(self, text):
        with self._log_lock:
            try:
                if os.path.exists(self.log_path) and \
                        os.path.getsize(self.log_path) > self.cfg["max_log_bytes"]:
                    os.replace(self.log_path, self.log_path + ".1")
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(time.strftime("%H:%M:%S ") + text + "\n")
            except OSError:
                pass

    def event(self, kind, **fields):
        """Structured log, for analysis later. Never spoken."""
        rec = dict(fields)
        rec["t"] = round(time.time(), 3)
        rec["kind"] = kind
        try:
            with open(self.events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass

    # ---- main loop -------------------------------------------------------

    def run(self):
        self._heartbeat()
        extra = " Type help to hear what you can ask me."
        self.announce(f"watching {os.path.basename(self.folder)}. "
                      "I'll tell you what the agent changes. "
                      "Press Enter any time to hear the current state." + extra,
                      urgent=True)
        last_beat = time.time()
        errors = 0
        while not self.stop.is_set():
            time.sleep(self.cfg["poll_seconds"])
            try:
                self._tick()
                errors = 0
            except Exception as e:
                errors += 1
                if errors == 1:
                    self.announce(f"I hit a problem while watching: {e}. "
                                  "I'm still going.", urgent=True)
                    self.event("watcher_error", error=str(e))
            if time.time() - last_beat >= HEARTBEAT_SECONDS:
                self._heartbeat()
                last_beat = time.time()

    def _heartbeat(self):
        """So `willow run` in another window can tell a watcher is actually
        here, and refuse to write results somewhere nobody is listening."""
        try:
            with open(self.watching_path, "w", encoding="utf-8") as f:
                json.dump({"folder": self.folder, "pid": os.getpid(),
                           "version": __version__, "t": time.time()}, f)
        except OSError:
            pass

    def _tick(self):
        changed, gone = self.eyes.look()
        if changed or gone:
            for rel in changed:
                if rel not in self.ep_touched:
                    self.ep_exists_before[rel] = rel in self.committed
                self.ep_touched[rel] = self.ep_touched.get(rel, 0) + 1
            for rel in gone:
                if rel not in self.ep_touched:
                    self.ep_exists_before[rel] = rel in self.committed
                self.ep_touched[rel] = self.ep_touched.get(rel, 0) + 1
            self.last_change_at = time.time()

        events = self._read_new_events()
        # Write events feed the current chunk; they don't end it. Only a
        # result or a notice does.
        for e in events:
            if e.get("type") == "write":
                self._record_write(e)
        closers = [e for e in events if e.get("type") != "write"]
        if closers:
            self._close_episode(closers)
            return

        if (self.ep_touched or self.ep_writes) and self.last_change_at is not None:
            if time.time() - self.last_change_at >= self.cfg["quiet_seconds"]:
                self._close_episode([])

    # ---- reading results written by `willow run`, hooks, or a script ------

    @staticmethod
    def _prefix_hash(path, nbytes):
        """Fingerprint of the part of the file we have already consumed."""
        if nbytes <= 0:
            return ""
        try:
            with open(path, "rb") as f:
                return hashlib.sha1(f.read(min(nbytes, 512))).hexdigest()
        except OSError:
            return ""

    def _tail_lines(self, path):
        """New COMPLETE lines since last time, surviving the file being rewritten.

        The first version kept a byte offset and never checked whether the file
        had been replaced, so any CI step that REWROTE the results file left the
        offset in the middle of the new content and Willow went silently deaf
        for the rest of the session. Checking the size only catches a rewrite
        that made the file shorter, so the part already consumed is
        fingerprinted too: if it no longer matches, the file was rewritten and
        we start again from the top.

        Reading in binary and stopping at the last newline also means a result
        being written at this instant is never parsed half-finished.
        """
        try:
            st = os.stat(path)
        except OSError:
            return []
        identity = (getattr(st, "st_ino", 0), getattr(st, "st_dev", 0))
        offset = 0
        state = self._tails.get(path)
        if state is not None:
            prev_offset, prev_identity, prev_prefix = state
            rewritten = (st.st_size < prev_offset
                         or identity != prev_identity
                         or self._prefix_hash(path, prev_offset) != prev_prefix)
            offset = 0 if rewritten else prev_offset
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
        except OSError:
            return []
        new_offset = offset + len(data)
        if data and not data.endswith(b"\n"):
            cut = data.rfind(b"\n")
            if cut == -1:
                return []               # a partial line; wait for the rest
            new_offset = offset + cut + 1
            data = data[:cut + 1]
        self._tails[path] = (new_offset, identity,
                             self._prefix_hash(path, new_offset))
        return data.decode("utf-8", errors="replace").splitlines()

    def _record_write(self, event):
        """An exact write, reported by the harness rather than counted by us."""
        path = event.get("path") or ""
        try:
            rel = os.path.relpath(os.path.abspath(path), self.folder)
        except ValueError:                    # a different drive on Windows
            return
        if rel.startswith("..") or is_ignored(rel, self.eyes.ignore_dirs):
            return
        n = int(event.get("count") or 1)
        self.ep_writes[rel] = self.ep_writes.get(rel, 0) + n
        self.last_change_at = time.time()

    def _read_new_events(self):
        """EVERY new result, in order.

        The first version kept only the last one it saw in a tick, so a failing
        lint followed by a passing test was announced as simply 'passed'. Losing
        a failure is as harmful as inventing a pass.
        """
        events = []
        for line in self._tail_lines(self.inbox_path):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        for line in self._tail_lines(self.results_path):
            parsed = parse_result_line(line)
            if parsed is not None:
                events.append(parsed)
        return events

    # ---- closing a chunk and deciding what to say (the RULEBOOK) ---------

    def _close_episode(self, events):
        touched = self.ep_touched
        exists_before = self.ep_exists_before
        writes_exact = self.ep_writes
        self.ep_touched = {}
        self.ep_writes = {}
        self.ep_exists_before = {}
        self.last_change_at = None

        checks = [e for e in events if e.get("type") == "check"]
        notes = [e for e in events if e.get("type") in ("launch_error", "notice")]

        now_hashes = self.eyes.snapshot_hashes()
        parts, details = [], []
        reverted = []
        episode_files = []

        # (a) pair up renames: a delete and a create sharing content
        renames = self._detect_renames(touched, exists_before, now_hashes)
        renamed_away = {old for old, _new in renames}
        renamed_to = {new for _old, new in renames}

        for old, new in renames:
            phrase = f"renamed {old} to {new}"
            parts.append(phrase)
            details.append(phrase)
            episode_files.append((new, self.committed_text.get(old),
                                  self.eyes.current_text(new), "renamed"))

        # (b) net change per file, plus churn and silent undos
        file_phrases = []
        # The union, not just what polling noticed: a file written twice and
        # left exactly as it started never differs at any tick Willow looks at,
        # so polling alone cannot see it. A hook can - and that A-to-B-to-A
        # case is the most worth hearing about, not the least.
        for rel in sorted(set(touched) | set(writes_exact)):
            if rel in renamed_away or rel in renamed_to:
                continue
            was_there = exists_before.get(rel, rel in self.committed)
            is_there = rel in now_hashes
            base_hash = self.committed.get(rel)
            cur_hash = now_hashes.get(rel)
            orig_hash = self.session_baseline.get(rel)
            writes = touched.get(rel, 0)
            before_text = self.committed_text.get(rel)
            after_text = self.eyes.current_text(rel)

            # Written, but the bytes ended up identical and it was only one
            # write: nothing actually happened, so say nothing.
            if (cur_hash == base_hash and is_there == was_there
                    and max(writes, writes_exact.get(rel, 0)) < 2):
                continue

            # A silent undo: changed more than once, ends exactly where it began.
            if max(writes, writes_exact.get(rel, 0)) >= 2                     and cur_hash == base_hash and is_there == was_there:
                reverted.append(f"quietly undid the change to {rel}")
                continue
            # Back to the content it had when this session started.
            if is_there and cur_hash == orig_hash and base_hash != orig_hash:
                reverted.append(f"put {rel} back to the version it had when we started")
                continue
            # Deleted something it had created during this session. That is a
            # deletion, not a return to an earlier version - there wasn't one.
            if was_there and not is_there and orig_hash is None:
                phrase = f"deleted {rel}, which it created earlier"
                file_phrases.append(phrase)
                episode_files.append((rel, before_text, None, "deleted"))
                continue

            phrase = describe_change(rel, was_there, is_there,
                                     before_text, after_text)
            # A count is only spoken when it is a real count. A hook reports
            # one event per actual write, so that number is a fact; polling can
            # only count the ticks in which the file happened to differ, which
            # is a different number and is not.
            exact = writes_exact.get(rel, 0)
            if exact >= 2:
                phrase += f", written {exact} times"
            elif writes >= 3:
                phrase += ", changed repeatedly"
            file_phrases.append(phrase)
            episode_files.append((rel, before_text, after_text,
                                  "created" if not was_there else
                                  "deleted" if not is_there else "edited"))

        details.extend(file_phrases)
        if len(file_phrases) == 1:
            parts.append(file_phrases[0])
        elif file_phrases:
            parts.append(f"{len(file_phrases)} files: " + "; ".join(file_phrases))

        # (c) silent undos, called out separately - easy to miss, worth hearing
        parts.extend(reverted)
        details.extend(reverted)

        # (d) things Willow could not verify - said plainly, never as a verdict
        for note in notes:
            parts.append(note.get("message", "something happened I couldn't check"))
            details.append(parts[-1])

        # (e) build/test outcomes: a hard fact from a real exit code
        any_failure = False
        for check in checks:
            code = normalize_exit_code(check.get("exit_code"))
            cmd = check.get("command", "the check")
            took = check.get("seconds")
            if code == 0:
                phrase = f"then '{cmd}' passed"
                if took:
                    phrase += f" in {took} seconds"
            else:
                any_failure = True
                phrase = f"then '{cmd}' FAILED (exit code {code})"
                detail = check.get("detail")
                if detail:
                    phrase += f". {detail}"
            parts.append(phrase)
            details.append(phrase)

        # (f) the derived "stuck" signal, counted from the log
        stuck_notes = self._update_stuck(checks, touched)

        self.committed = now_hashes
        self.committed_text = dict(self.eyes.text)
        self.last_episode = {"files": episode_files, "checks": checks,
                             "at": time.time()}
        self.session["episodes"] += 1
        self.session["checks"] += len(checks)
        self.session["failures"] += sum(1 for c in checks if c.get("exit_code") != 0)
        self.session["files"].update(touched)

        if parts:
            line = ". ".join(parts) + "."
            urgent = any_failure or bool(reverted) or bool(notes)
            self.announce_lines(line, details, urgent=urgent, narratable=True)
            self.event("episode", facts=line, files=sorted(touched),
                       checks=checks, reverted=reverted)
        for note in stuck_notes:
            self.announce(note + ".", urgent=True)
            self.event("stuck", facts=note)

    def _detect_renames(self, touched, exists_before, now_hashes):
        """A delete and a create in the same chunk with identical content is a
        rename - and Willow already holds both hashes, so this is certain."""
        created, deleted = {}, {}
        for rel in touched:
            was = exists_before.get(rel, rel in self.committed)
            is_now = rel in now_hashes
            if not was and is_now:
                created[now_hashes[rel]] = rel
            elif was and not is_now:
                h = self.committed.get(rel)
                if h:
                    deleted[h] = rel
        pairs = []
        for h, old in deleted.items():
            if h in created:
                pairs.append((old, created[h]))
        return sorted(pairs)

    def _update_stuck(self, checks, touched):
        """Count failing attempts per (command, file).

        The first version incremented a counter for every file touched in ANY
        failing chunk, with no link between the file and the check - so editing
        a README while an unrelated suite was red produced 'stuck on README.md'.
        A pass also only reset the files in the passing chunk, so 'in a row'
        wasn't true either.
        """
        notes = []
        threshold = self.cfg["stuck_threshold"]
        for check in checks:
            cmd = check.get("command", "the check")
            if check.get("exit_code") == 0:
                for key in list(self.fail_streak):
                    if key[0] == cmd:
                        self.fail_streak.pop(key)
                        self.stuck_announced.discard(key)
                continue
            for rel in touched:
                key = (cmd, rel)
                self.fail_streak[key] = self.fail_streak.get(key, 0) + 1
                n = self.fail_streak[key]
                if n >= threshold and key not in self.stuck_announced:
                    self.stuck_announced.add(key)
                    notes.append(f"the agent has changed {rel} in {n} attempts "
                                 f"that each ended with '{cmd}' failing")
        return notes

    # ---- the pull channel: ask any time ----------------------------------

    def state_summary(self):
        """Answered from what the watcher already knows, so it is instant."""
        now = self.eyes.snapshot_hashes()
        created = [r for r in now if r not in self.session_baseline]
        deleted = [r for r in self.session_baseline if r not in now]
        modified = [r for r in now if r in self.session_baseline
                    and now[r] != self.session_baseline[r]]
        bits = []
        if created:
            bits.append(f"{len(created)} new file{'s' if len(created) != 1 else ''}")
        if modified:
            bits.append(f"{len(modified)} changed file{'s' if len(modified) != 1 else ''}")
        if deleted:
            bits.append(f"{len(deleted)} deleted file{'s' if len(deleted) != 1 else ''}")
        if not bits:
            msg = "state right now: nothing has changed since we started."
        else:
            msg = "state right now: " + ", ".join(bits) + " since we started"
            stuck = sorted({rel for (_c, rel), n in self.fail_streak.items()
                            if n >= self.cfg["stuck_threshold"]})
            if stuck:
                msg += ". Possibly stuck on: " + ", ".join(stuck)
            msg += "."
        self.announce(msg, urgent=True, narratable=True)

    def recap(self):
        s = self.session
        mins = max(1, int((time.time() - s["started"]) / 60))
        bits = [f"{len(s['files'])} file{'s' if len(s['files']) != 1 else ''} touched",
                f"{s['episodes']} chunk{'s' if s['episodes'] != 1 else ''} of work"]
        if s["checks"]:
            bits.append(f"{s['checks']} build or test run{'s' if s['checks'] != 1 else ''}, "
                        f"{s['failures']} of them failing")
        else:
            bits.append("no build or test result has reached me")
        busiest = None
        counts = {}
        for (_cmd, rel), n in self.fail_streak.items():
            counts[rel] = max(counts.get(rel, 0), n)
        if counts:
            busiest = max(counts, key=counts.get)
        msg = f"session recap, {mins} minute{'s' if mins != 1 else ''} in: " + ", ".join(bits)
        if busiest:
            msg += f". Most failing attempts: {busiest}"
        self.announce(msg + ".", urgent=True)

    def read_diff(self):
        """Read out what actually changed, rather than only how much."""
        ep = self.last_episode
        if not ep or not ep["files"]:
            self.announce("I don't have a recent change to read out yet.",
                          urgent=True)
            return
        shown = 0
        print("Willow: the last chunk, line by line:", flush=True)
        for rel, before, after, kind in ep["files"]:
            print(f"        {kind} {rel}", flush=True)
            if before is None or after is None:
                print("          (no line-by-line view for this file)", flush=True)
                continue
            diff = list(difflib.unified_diff(
                before.splitlines(), after.splitlines(),
                fromfile="before", tofile="after", lineterm="", n=1))
            for line in diff[2:60]:
                print("          " + line, flush=True)
            if len(diff) > 60:
                print(f"          ... {len(diff) - 60} more diff lines", flush=True)
            shown += 1
        self.announce(f"read out the changes to {shown} file"
                      f"{'s' if shown != 1 else ''} above; "
                      "they're on screen for your screen reader.", urgent=True)

    def describe_view(self):
        if self.viewer is None:
            self.announce("describing the app isn't switched on right now.",
                          urgent=True)
            return
        out_path = os.path.join(self.folder, WILLOW_DIR, "last_view.png")
        self.announce("looking at your app now...", urgent=True)
        for line in self.viewer.describe(os.path.basename(self.folder), out_path,
                                         keep=self.cfg.get("keep_captures")):
            self.announce(line, urgent=True)

    def describe_other_window(self):
        if self.viewer is None:
            return
        self.viewer.next_window()
        self.describe_view()


# ==========================================================================
# Finding the folder a watcher is actually watching
# ==========================================================================

def find_willow_dir(start):
    """The nearest .willow from `start` upward, or None."""
    cur = os.path.abspath(start)
    while True:
        candidate = os.path.join(cur, WILLOW_DIR)
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def watcher_status(willow_dir):
    """(is_live, folder) for the watcher that owns this .willow, if any."""
    try:
        with open(os.path.join(willow_dir, WATCHING_NAME), "r", encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False, None
    age = time.time() - float(info.get("t", 0))
    return age < WATCHER_STALE_SECONDS, info.get("folder")


def resolve_target_dir(start="."):
    """Where a result should be delivered, and whether anyone is listening.

    The first version searched upward and, finding nothing, quietly created a
    .willow in the current folder and wrote there - so a result could land
    somewhere no watcher would ever read it, and the user simply heard nothing.
    """
    env = os.environ.get("WILLOW_DIR")
    if env:
        os.makedirs(env, exist_ok=True)
        live, _ = watcher_status(env)
        return env, live
    found = find_willow_dir(start)
    if found:
        live, _ = watcher_status(found)
        return found, live
    fallback = os.path.join(os.path.abspath(start), WILLOW_DIR)
    return fallback, False


def deliver(record, start=".", warn=True):
    """Put a verified record where the watcher will find it."""
    target, live = resolve_target_dir(start)
    try:
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, INBOX_NAME), "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        print(f"Willow: couldn't record the result ({e}).", file=sys.stderr)
        return False
    if not live and warn:
        print("Willow: WARNING - no Willow is watching this folder, so this "
              "result will NOT be announced out loud.\n"
              "        Start one with:  willow watch\n"
              f"        (result was recorded in {target})", file=sys.stderr)
    return live


# ==========================================================================
# Commands
# ==========================================================================

def cmd_watch(argv):
    narrate = False
    overrides = {}
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--narrate", "-n"):
            narrate = True
        elif a == "--quiet":
            overrides["verbosity"] = "quiet"
        elif a == "--no-voice":
            overrides["voice"] = False
        elif a in ("--provider", "--model", "--base-url", "--rate", "--poll") and i + 1 < len(argv):
            key = {"--provider": "provider", "--model": "text_model",
                   "--base-url": "base_url", "--rate": "voice_rate",
                   "--poll": "poll_seconds"}[a]
            val = argv[i + 1]
            overrides[key] = (int(val) if key == "voice_rate"
                              else float(val) if key == "poll_seconds" else val)
            i += 1
        else:
            positional.append(a)
        i += 1

    folder = os.path.abspath(positional[0] if positional else ".")
    if not os.path.isdir(folder):
        print(f"Willow: that folder does not exist: {folder}")
        return 1
    os.makedirs(os.path.join(folder, WILLOW_DIR), exist_ok=True)

    cfg = load_config(folder, overrides)
    provider = resolve_provider(cfg)

    narrator = None
    if narrate:
        if provider.available():
            narrator = Narrator(provider, timeout=cfg["narrate_timeout"])
            print(f"Willow: natural narration is on, using {provider.label}. "
                  "I'll say the same verified facts more naturally, and throw "
                  "away anything the model says that the facts don't support.")
            if isinstance(provider, OllamaProvider):
                threading.Thread(target=provider.warm_up, daemon=True).start()
        else:
            print("Willow: (note) narration is on, but " + (provider.problem() or
                  "no model backend is available") + ". Using plain wording.")

    speaker = Speaker(enabled=cfg["voice"], rate=cfg["voice_rate"],
                      narrator=narrator, use_host=cfg.get("voice_host", True))
    viewer = Viewer(provider, timeout=cfg["view_timeout"])

    # Reading the project once takes a moment on a large one. Say so, because
    # silence here is indistinguishable from Willow having failed to start -
    # and anything the agent changes before the "watching" line is treated as
    # how the project already was.
    print("Willow: starting up, taking a first look at the project...", flush=True)
    speaker.say("Willow starting up.", priority=P_URGENT)
    willow = Willow(folder, speaker, cfg, viewer=viewer)
    speaker.on_spoken = willow.on_spoken

    worker = threading.Thread(target=willow.run, daemon=True)
    worker.start()

    HELP_LINES = [
        "press Enter for the current state",
        "diff      - read out what changed in the last chunk",
        "recap     - a summary of the whole session",
        "describe  - what does my app look like right now",
        "other     - describe a different window instead",
        "stop      - stop talking (also: shh)",
        "faster / slower  - change the speaking rate",
        "quiet / loud     - speak only the important things, or everything",
        "q         - quit",
    ]

    try:
        for line in sys.stdin:
            cmd = line.strip().lower()
            if cmd in ("q", "quit", "exit"):
                break
            elif cmd in ("stop", "shh", "shut up", "quiet!", "x"):
                speaker.stop_talking()
                print("Willow: (quiet)", flush=True)
            elif cmd in ("describe", "desc", "see", "look", "d"):
                threading.Thread(target=willow.describe_view, daemon=True).start()
            elif cmd in ("other", "another", "wrong window", "next"):
                threading.Thread(target=willow.describe_other_window, daemon=True).start()
            elif cmd in ("diff", "changes", "what changed"):
                willow.read_diff()
            elif cmd in ("recap", "summary", "session"):
                willow.recap()
            elif cmd in ("faster", "f", "+"):
                r = speaker.set_rate(speaker.rate + 1)
                willow.announce(f"speaking rate {r}.", urgent=True)
            elif cmd in ("slower", "-"):
                r = speaker.set_rate(speaker.rate - 1)
                willow.announce(f"speaking rate {r}.", urgent=True)
            elif cmd in ("quiet", "less"):
                willow.quiet = True
                willow.announce("I'll only speak the important things now. "
                                "Everything still appears as text.", urgent=True)
            elif cmd in ("loud", "more", "normal"):
                willow.quiet = False
                willow.announce("I'll speak everything again.", urgent=True)
            elif cmd in ("help", "h", "?"):
                for h in HELP_LINES:
                    print("        " + h, flush=True)
                speaker.say("You can ask for the state, diff, recap, describe, "
                            "stop, faster, slower, quiet, loud, or q to quit.",
                            priority=P_URGENT)
            elif cmd in ("", "state", "status", "s"):
                willow.state_summary()
            else:
                willow.state_summary()
    except KeyboardInterrupt:
        pass
    finally:
        willow.stop.set()
        speaker.stop_talking()
        speaker.close()
        try:
            os.remove(willow.watching_path)
        except OSError:
            pass
        print("Willow: stopped watching. Goodbye.")
    return 0


def cmd_run(argv):
    """Run a build/test command and report the REAL result."""
    capture = True
    if argv and argv[0] == "--no-capture":
        capture, argv = False, argv[1:]
    if not argv:
        print("Willow: tell me what to run, e.g. willow run npm test")
        return 1

    command_str = " ".join(argv)

    # Resolve the program properly. On Windows a bare `npm` is `npm.cmd`, which
    # CreateProcess will not find - the first version reported that as the test
    # suite FAILING with exit code 127. A command that cannot be launched has
    # no pass or fail; saying it failed would be exactly the kind of unchecked
    # claim Willow exists to avoid.
    exe = shutil.which(argv[0])
    if exe is None:
        msg = (f"I couldn't run '{command_str}' - the command '{argv[0]}' "
               "wasn't found on this computer. That's not a test failure; it "
               "just didn't run.")
        print("Willow: " + msg, file=sys.stderr)
        deliver({"ts": time.time(), "type": "launch_error",
                 "command": command_str, "message": msg})
        return 127

    print(f"Willow: running '{command_str}' ...", flush=True)
    start = time.time()
    tail = []
    try:
        if capture:
            env = dict(os.environ, PYTHONUNBUFFERED="1", NO_COLOR="1",
                       FORCE_COLOR="0")
            proc = subprocess.Popen([exe] + argv[1:], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace",
                                    bufsize=1, env=env)
            for line in proc.stdout:
                sys.stdout.write(line)      # the agent still sees everything
                tail.append(line)
                if len(tail) > 600:
                    del tail[:200]
            proc.wait()
            exit_code = proc.returncode
        else:
            exit_code = subprocess.run([exe] + argv[1:]).returncode
    except KeyboardInterrupt:
        print("Willow: the command was cancelled.")
        deliver({"ts": time.time(), "type": "launch_error",
                 "command": command_str,
                 "message": f"'{command_str}' was cancelled before it finished"})
        return 130
    except OSError as e:
        msg = f"I couldn't run '{command_str}': {e}. That's not a test failure."
        print("Willow: " + msg, file=sys.stderr)
        deliver({"ts": time.time(), "type": "launch_error",
                 "command": command_str, "message": msg})
        return 126

    took = round(time.time() - start, 1)
    exit_code = normalize_exit_code(exit_code)
    detail = summarize_output("".join(tail), exit_code) if capture else None
    record = {"ts": time.time(), "type": "check", "command": command_str,
              "exit_code": exit_code, "seconds": took}
    if detail:
        record["detail"] = detail
    deliver(record)

    verdict = "passed" if exit_code == 0 else f"FAILED (exit code {exit_code})"
    extra = f" {detail}" if detail else ""
    print(f"Willow: '{command_str}' {verdict} in {took}s.{extra}", flush=True)
    return exit_code


def cmd_hook(argv):
    """Receive an event from Claude Code on stdin.

    A hook is run by the harness, not by the model, so what it reports is an
    external fact in exactly the way `willow run` is - it is not the agent's
    account of its own work. But if the payload does not contain an
    unambiguous exit code, Willow reports only that a command RAN. It never
    converts an unknown into a verdict.
    """
    event = argv[0] if argv else ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        payload = {}

    cwd = payload.get("cwd") or "."
    tool = payload.get("tool_name", "")
    tin = payload.get("tool_input") or {}
    tout = payload.get("tool_response") or payload.get("tool_result") or {}
    if not isinstance(tout, dict):
        tout = {"output": tout}

    if event == "Notification":
        msg = payload.get("message") or "Claude needs your attention."
        deliver({"ts": time.time(), "type": "notice",
                 "message": "Claude says: " + str(msg)}, cwd, warn=False)
        return 0

    if event == "Stop":
        deliver({"ts": time.time(), "type": "notice",
                 "message": "Claude has finished its turn"}, cwd, warn=False)
        return 0

    if event == "PostToolUse" and tool in ("Edit", "Write", "MultiEdit",
                                           "NotebookEdit"):
        # One event per actual write, straight from the harness. This is what
        # lets Willow say "edited 5 times" as a FACT - polling can only count
        # the ticks in which a file happened to differ, which is not the same
        # number and must not be spoken as if it were.
        path = tin.get("file_path") or tin.get("notebook_path") or ""
        if not path:
            return 0
        n = len(tin.get("edits") or []) if tool == "MultiEdit" else 1
        deliver({"ts": time.time(), "type": "write", "path": path,
                 "tool": tool, "count": max(1, n)}, cwd, warn=False)
        return 0

    if event == "PostToolUse" and tool == "Bash":
        command = str(tin.get("command", "")).strip()
        if not command:
            return 0
        code = None
        for key in ("exit_code", "exitCode", "returncode", "code", "status"):
            if isinstance(tout.get(key), int):
                code = tout[key]
                break
        if code is None:
            # No verifiable exit code in the payload. Say what ran, not how it
            # went. `willow run` remains the authoritative source of verdicts.
            deliver({"ts": time.time(), "type": "notice",
                     "message": f"the agent ran '{command[:90]}' (I couldn't "
                                "check whether it passed)"}, cwd, warn=False)
            return 0
        detail = summarize_output(str(tout.get("stdout", "")) +
                                  str(tout.get("stderr", "")), code)
        rec = {"ts": time.time(), "type": "check", "command": command[:120],
               "exit_code": code}
        if detail:
            rec["detail"] = detail
        deliver(rec, cwd, warn=False)
        return 0

    return 0


def _gitignore_has(path, entry):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return any(ln.strip().rstrip("/") == entry.rstrip("/")
                       for ln in f)
    except OSError:
        return False


CLAUDE_MD_TEMPLATE = """<!-- willow:start -->
# Working notes for Claude Code (Willow is watching this project)

Willow is a companion tool that narrates your work out loud to a blind
developer. Willow watches the files by itself, so you do not need to do anything
special for file changes.

**One rule:** so the developer can hear whether builds and tests actually pass
or fail, **run every build or test command through Willow**, like this:

    {command} run <your command>

For example:

    {command} run npm test
    {command} run python -m pytest

Please do **not** run the raw command on its own - always wrap it with
`{command} run ...`. This captures the real exit code, so the developer hears an
honest, verifiable outcome rather than your description of it.

Everything else you do normally is fine.
<!-- willow:end -->
"""


def self_command():
    """How to invoke Willow again from somewhere else, as short as is safe.

    This string is written into the project's CLAUDE.md and into the Claude Code
    hooks, so it has to keep working from a different working directory and a
    different shell. Preferring the bare name when it really is on PATH keeps
    the instruction readable; otherwise fall back to something absolute.
    """
    argv0 = sys.argv[0] or ""
    launched_as_command = os.path.basename(argv0).lower() in ("willow", "willow.exe")
    on_path = shutil.which("willow")
    if on_path:
        return "willow"
    if launched_as_command and os.path.isabs(argv0):
        return f'"{argv0}"'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def cmd_init(argv):
    """Set a project up, so nothing has to be edited by hand."""
    install_hooks = "--no-hooks" not in argv
    positional = [a for a in argv if not a.startswith("-")]
    folder = os.path.abspath(positional[0] if positional else ".")
    if not os.path.isdir(folder):
        print(f"Willow: that folder does not exist: {folder}")
        return 1

    done = []
    os.makedirs(os.path.join(folder, WILLOW_DIR), exist_ok=True)
    os.makedirs(user_config_dir(), exist_ok=True)

    command = self_command()

    # 1. project config
    cfg_path = os.path.join(folder, WILLOW_DIR, "config.json")
    if not os.path.exists(cfg_path):
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({k: DEFAULTS[k] for k in
                       ("voice_rate", "verbosity", "provider", "text_model",
                        "vision_model", "poll_seconds")}, f, indent=2)
        done.append(f"wrote settings you can change in {WILLOW_DIR}/config.json")

    # 2. .gitignore
    gi = os.path.join(folder, ".gitignore")
    if not _gitignore_has(gi, WILLOW_DIR):
        try:
            newline = ""
            if os.path.exists(gi) and os.path.getsize(gi) > 0:
                with open(gi, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    newline = "" if f.read(1) == b"\n" else "\n"
            with open(gi, "a", encoding="utf-8") as f:
                f.write(newline + "\n# Willow's own workspace\n.willow/\n")
            done.append("added .willow/ to .gitignore so it never gets committed")
        except OSError:
            pass

    # 3. CLAUDE.md, with the real path already filled in
    block = CLAUDE_MD_TEMPLATE.format(command=command)
    cm = os.path.join(folder, "CLAUDE.md")
    existing = ""
    try:
        with open(cm, "r", encoding="utf-8") as f:
            existing = f.read()
    except OSError:
        pass
    if "<!-- willow:start -->" in existing:
        # A lambda replacement, so backslashes in a Windows path are not read
        # as regex escapes.
        new = re.sub(r"<!-- willow:start -->.*?<!-- willow:end -->\n?",
                     lambda _m: block, existing, flags=re.S)
        verb = "updated"
    elif existing.strip():
        new = existing.rstrip() + "\n\n" + block
        verb = "added the Willow section to"
    else:
        new = block
        verb = "created"
    try:
        with open(cm, "w", encoding="utf-8") as f:
            f.write(new)
        done.append(f"{verb} CLAUDE.md, with the right path already filled in")
    except OSError as e:
        print(f"Willow: couldn't write CLAUDE.md ({e})")

    # 4. Claude Code hooks - these fire from the harness, not the model, so
    #    they work even when the agent forgets the CLAUDE.md rule.
    if install_hooks:
        try:
            done.append(_install_hooks(folder, command))
        except Exception as e:
            print(f"Willow: couldn't install Claude Code hooks ({e}). "
                  "That's fine - the CLAUDE.md rule still works.")

    print("Willow: this project is set up.")
    for d in done:
        if d:
            print("  - " + d)
    print("\nNext, in this window:")
    print("    " + command + " watch")
    print("Then work with your AI agent in another window.")
    print("\nIf anything seems wrong, run:  " + command + " doctor")

    speaker = Speaker(enabled=True, rate=load_config(folder)["voice_rate"])
    speaker.say("Willow is set up for this project. Run willow watch to start "
                "listening.", priority=P_URGENT)
    time.sleep(4)
    speaker.close()
    return 0


def _hook_command(command, event):
    return f"{command} hook {event}"


def _install_hooks(folder, command):
    """Merge Willow's hooks into .claude/settings.json without clobbering."""
    path = os.path.join(folder, ".claude", "settings.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    hooks = data.setdefault("hooks", {})

    wanted = [
        # Real exit codes, even when the agent forgets the CLAUDE.md rule.
        ("PostToolUse", "Bash"),
        # One event per actual write, so repeated editing can be reported as a
        # counted fact instead of guessed at from how often polling noticed.
        ("PostToolUse", "Edit|Write|MultiEdit|NotebookEdit"),
        # "Claude is waiting for permission" - invisible if you can't see it.
        ("Notification", None),
        ("Stop", None),
    ]

    def already_there(entries, cmd, matcher):
        # Compare the actual command strings, not their JSON encoding - a
        # Windows path comes back with doubled backslashes and would never
        # match, so the hook got appended again on every init.
        for entry in entries:
            if (entry or {}).get("matcher") != matcher:
                continue
            for hook in (entry or {}).get("hooks", []):
                if isinstance(hook, dict) and hook.get("command") == cmd:
                    return True
        return False

    added = []
    for event, matcher in wanted:
        cmd = _hook_command(command, event)
        entries = hooks.setdefault(event, [])
        if already_there(entries, cmd, matcher):
            continue
        entry = {"hooks": [{"type": "command", "command": cmd}]}
        if matcher:
            entry["matcher"] = matcher
        entries.append(entry)
        added.append(event if not matcher else f"{event}:{matcher.split('|')[0]}")

    if not added:
        return "Claude Code hooks were already installed"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return ("installed Claude Code hooks (" + ", ".join(added) + ") so you also "
            "hear when Claude is waiting for you")


def cmd_doctor(argv):
    """Check everything, out loud - because every failure here is silence."""
    folder = os.path.abspath(argv[0] if argv and not argv[0].startswith("-") else ".")
    cfg = load_config(folder)
    lines = []
    problems = []

    lines.append(f"Willow {__version__} on {platform.system()}, "
                 f"Python {sys.version_info.major}.{sys.version_info.minor}")
    if sys.version_info < (3, 8):
        problems.append("Python 3.8 or newer is needed")

    # voice
    engine = ("Windows built-in voice" if IS_WINDOWS else
              "macOS say" if IS_MAC else
              "spd-say" if shutil.which("spd-say") else
              "espeak" if shutil.which("espeak") else None)
    if engine:
        lines.append(f"Voice: {engine}, rate {cfg['voice_rate']} "
                     "(change with: willow watch --rate 5)")
    else:
        lines.append("Voice: NOT AVAILABLE")
        problems.append("no speech engine found. On Linux install one of "
                        "speech-dispatcher (spd-say) or espeak")

    # model backend
    provider = resolve_provider(cfg)
    if provider.available():
        lines.append(f"Model backend: {provider.label}")
        if isinstance(provider, OllamaProvider):
            models = provider.installed() or []
            lines.append(f"  installed locally: {', '.join(models[:6]) or 'none'}")
            lines.append(f"  narration will use: {provider.pick_text()}")
            lines.append(f"  describe will use: {provider.pick_vision() or 'nothing yet'}")
            if not provider.pick_vision():
                lines.append("  for describe, run: ollama pull "
                             + provider.RECOMMENDED)
        elif provider.supports_vision():
            lines.append("  can describe how your app looks: yes")
    else:
        lines.append("Model backend: none (optional)")
        lines.append("  Plain wording and all facts work without one. For "
                     "natural narration or describe, either set an API key, or "
                     "install Ollama from ollama.com and run: ollama pull "
                     + OllamaProvider.RECOMMENDED)

    # this project
    wd = os.path.join(folder, WILLOW_DIR)
    lines.append(f"Project: {folder}")
    if os.path.isdir(wd):
        live, watched = watcher_status(wd)
        lines.append("  a Willow IS watching this folder right now" if live
                     else "  no Willow is watching this folder right now")
    else:
        lines.append("  not set up yet - run: willow init")
        problems.append("this project has not been set up. Run willow init")

    cm = os.path.join(folder, "CLAUDE.md")
    try:
        with open(cm, "r", encoding="utf-8") as f:
            content = f.read()
        if "willow" in content.lower() and "<PATH-TO>" not in content:
            lines.append("  CLAUDE.md tells the agent to use Willow: yes")
        elif "<PATH-TO>" in content:
            lines.append("  CLAUDE.md still has a placeholder path")
            problems.append("CLAUDE.md has an unfilled <PATH-TO> placeholder. "
                            "Run willow init to fix it")
        else:
            lines.append("  CLAUDE.md does not mention Willow")
            problems.append("CLAUDE.md has no Willow rule. Run willow init")
    except OSError:
        lines.append("  no CLAUDE.md")
        problems.append("there is no CLAUDE.md. Run willow init")

    hooks_path = os.path.join(folder, ".claude", "settings.json")
    try:
        with open(hooks_path, "r", encoding="utf-8") as f:
            lines.append("  Claude Code hooks installed: "
                         + ("yes" if "willow" in f.read().lower() else "no"))
    except OSError:
        lines.append("  Claude Code hooks installed: no")

    if not _gitignore_has(os.path.join(folder, ".gitignore"), WILLOW_DIR):
        lines.append("  .willow/ is NOT in .gitignore")

    for ln in lines:
        print(ln)
    if problems:
        print("\nThings to fix:")
        for p in problems:
            print("  * " + p)
    else:
        print("\nEverything looks ready.")

    speaker = Speaker(enabled=cfg["voice"], rate=cfg["voice_rate"])
    spoken = "Willow check. " + (
        "Everything looks ready. " if not problems else
        f"{len(problems)} thing{'s' if len(problems) != 1 else ''} to fix. "
        + " Also, ".join(problems) + ". ")
    spoken += ("Natural narration and describe are available."
               if provider.available() else
               "Facts and speech work; natural narration is not set up, which is optional.")
    speaker.say(spoken, priority=P_URGENT)
    print("\n(speaking the result...)")
    time.sleep(min(25, 3 + len(spoken) / 14))
    speaker.close()
    return 1 if problems else 0


HELP = f"""Willow {__version__} - a companion narrator for blind builders.

  willow init [folder]      Set a project up. Do this once, per project.
  willow watch [folder]     Start watching (leave this window open).
  willow run <command>      Run a build/test command through Willow so it can
                            report the real pass/fail, e.g. willow run npm test
  willow doctor [folder]    Check everything works, and say the result out loud.

Options for `watch`:
  --narrate                 Say the same verified facts in a more natural voice.
                            Anything the model says that the facts don't support
                            is thrown away.
  --provider NAME           auto | anthropic | openai | ollama | openai-compat
                            `ollama` runs a model on THIS computer: no key, no
                            internet, and nothing about your project leaves it.
                            Suggested model: ollama pull gemma4
  --model NAME              Which model to use.
  --base-url URL            For any OpenAI-compatible server.
  --rate N                  Speaking rate, -10 (slow) to 10 (fast). Default 3.
  --quiet                   Speak only the important things; print everything.
  --no-voice                Text only.

While watching, type: (Enter) state, diff, recap, describe, other, stop,
faster, slower, quiet, loud, help, q.

Settings live in <project>/.willow/config.json and ~/.willow/config.json.
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(HELP)
        return 0
    if argv[0] in ("-V", "--version", "version"):
        print("willow " + __version__)
        return 0
    cmd, rest = argv[0].lower(), argv[1:]
    if cmd == "watch":
        return cmd_watch(rest)
    if cmd == "run":
        return cmd_run(rest)
    if cmd == "init":
        return cmd_init(rest)
    if cmd == "doctor":
        return cmd_doctor(rest)
    if cmd == "hook":
        return cmd_hook(rest)
    print(HELP)
    return 1


if __name__ == "__main__":
    sys.exit(main())
