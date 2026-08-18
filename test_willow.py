#!/usr/bin/env python3
"""
Willow's test suite.

Two jobs:

  1. GOLDEN TESTS over the rulebook. RULES.md is the thing Willow actually
     promises, so every rule on that page has a test here that pins the exact
     sentence it produces. If the code and the rulebook ever drift apart, this
     fails.

  2. REGRESSION TESTS for every defect found in review. Each one is named for
     what Willow used to say wrongly, so it stays fixed.

Run it (through Willow, so the real result gets announced):

    willow run python -m unittest discover -v

Standard library only, like Willow itself.
"""

import io
import os
import json
import time
import shutil
import tempfile
import unittest
import contextlib

import willow as W


# --------------------------------------------------------------------------
# Test harness
# --------------------------------------------------------------------------

class SilentSpeaker:
    """A Speaker that records instead of speaking."""

    def __init__(self):
        self.spoken = []
        self.rate = 3
        self.on_spoken = None

    def say(self, text, priority=W.P_NORMAL, narratable=False):
        self.spoken.append((priority, text))

    def stop_talking(self):
        self.spoken.append((W.P_URGENT, "<stop>"))

    def set_rate(self, r):
        self.rate = r
        return r

    def close(self):
        pass


class WillowCase(unittest.TestCase):
    """A temp project with a Willow watching it, driven tick by tick."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="willow-test-")
        self.speaker = SilentSpeaker()
        self.said = []
        # Keep tests hermetic and fast: no git, no real clock waits.
        self._real_git = W.git_ignored_dirs
        W.git_ignored_dirs = lambda folder: set()
        self.addCleanup(self._restore)

    def _restore(self):
        W.git_ignored_dirs = self._real_git
        shutil.rmtree(self.dir, ignore_errors=True)

    def start(self, **cfg_overrides):
        cfg = dict(W.DEFAULTS)
        cfg.update({"quiet_seconds": 0.01, "poll_seconds": 0.01})
        cfg.update(cfg_overrides)
        self.w = W.Willow(self.dir, self.speaker, cfg)
        self.said = []

        def capture(text, urgent=False, narratable=False):
            self.said.append(text)
        self.w.announce = capture
        self.w.announce_lines = lambda header, details, urgent=False, narratable=False: \
            self.said.append(header)
        return self.w

    # -- driving ----------------------------------------------------------

    def write(self, rel, content, bump=True):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        if bump:
            # Guarantee the modification is visible even when two writes land
            # inside one coarse filesystem clock tick with the same length.
            self._bumped = getattr(self, "_bumped", time.time()) + 2
            os.utime(path, (self._bumped, self._bumped))
        return path

    def remove(self, rel):
        os.remove(os.path.join(self.dir, rel))

    def tick(self):
        self.w._tick()

    def settle(self):
        """One tick to notice changes, then close the chunk on a pause."""
        self.w._tick()
        if self.w.ep_touched or self.w.ep_writes:
            self.w.last_change_at = time.time() - 1000
            self.w._tick()

    def check(self, exit_code, command="npm test", **extra):
        rec = {"type": "check", "command": command, "exit_code": exit_code}
        rec.update(extra)
        with open(self.w.inbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def hook_write(self, rel, count=1):
        """What a Claude Code PostToolUse hook on Edit/Write delivers."""
        with open(self.w.inbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "write", "tool": "Edit", "count": count,
                                "path": os.path.join(self.dir, rel)}) + "\n")

    def tray(self, line, rewrite=False):
        mode = "w" if rewrite else "a"
        with open(self.w.results_path, mode, encoding="utf-8") as f:
            f.write(line + "\n")

    @property
    def transcript(self):
        return " | ".join(self.said)


# ==========================================================================
# GOLDEN TESTS - one per rule in RULES.md
# ==========================================================================

class TestRulebook(WillowCase):

    def test_created_file(self):
        self.start()
        self.write("index.html", "<h1>hi</h1>\n")
        self.settle()
        self.assertIn("created index.html", self.transcript)

    def test_edited_file_reports_net_lines(self):
        self.write("app.py", "a\nb\nc\n")
        self.start()
        self.write("app.py", "a\nb\nc\nd\ne\n")
        self.settle()
        self.assertIn("edited app.py (+2 lines)", self.transcript)

    def test_edited_file_reports_additions_and_removals(self):
        self.write("app.py", "one\ntwo\nthree\n")
        self.start()
        self.write("app.py", "one\nCHANGED\n")
        self.settle()
        self.assertRegex(self.transcript, r"edited app\.py \(\+1 -2 lines\)")

    def test_deleted_file(self):
        self.write("gone.txt", "bye\n")
        self.start()
        self.remove("gone.txt")
        self.settle()
        self.assertIn("deleted gone.txt", self.transcript)

    def test_multiple_files_are_collapsed(self):
        self.start()
        self.write("a.js", "1\n")
        self.write("b.css", "2\n")
        self.settle()
        self.assertIn("2 files:", self.transcript)

    def test_churn_is_called_out_without_a_fabricated_count(self):
        self.write("busy.py", "v0\n")
        self.start()
        for i in range(4):
            self.write("busy.py", "v%d\n" % (i + 1))
            self.tick()
        self.w.last_change_at = time.time() - 1000
        self.tick()
        self.assertIn("changed repeatedly", self.transcript)

    def test_exact_write_counts_are_spoken_as_a_number(self):
        """With a hook reporting each write, the count is a fact - so say it."""
        self.write("app.py", "start\n")
        self.start()
        for i in range(5):
            self.write("app.py", "revision %d\n" % i, bump=False)
            self.hook_write("app.py")
        self.write("app.py", "revision 4\n")
        self.settle()
        self.assertIn("written 5 times", self.transcript)

    def test_a_burst_inside_one_tick_is_still_counted_by_the_hook(self):
        """Polling alone sees five rewrites in 0.7 s as one change."""
        self.write("app.py", "start\n")
        self.start()
        for i in range(5):
            self.write("app.py", "revision %d\n" % i, bump=(i == 4))
            self.hook_write("app.py")
        self.settle()
        self.assertIn("written 5 times", self.transcript)

    def test_without_a_hook_no_number_is_invented(self):
        self.write("busy.py", "v0\n")
        self.start()
        for i in range(4):
            self.write("busy.py", "v%d\n" % (i + 1))
            self.tick()
        self.w.last_change_at = time.time() - 1000
        self.tick()
        self.assertIn("changed repeatedly", self.transcript)
        self.assertNotRegex(self.transcript, r"\d+ times")

    def test_a_write_event_does_not_end_the_chunk(self):
        self.start()
        self.write("a.py", "x\n")
        self.hook_write("a.py")
        self.tick()
        self.assertEqual(self.said, [])          # still gathering
        self.settle()
        self.assertIn("created a.py", self.transcript)

    def test_writes_to_files_outside_the_project_are_ignored(self):
        self.start()
        self.w._record_write({"type": "write",
                              "path": os.path.join(tempfile.gettempdir(),
                                                   "elsewhere.py")})
        self.assertEqual(self.w.ep_writes, {})

    def test_a_hook_burst_that_nets_to_nothing_is_still_a_silent_undo(self):
        self.write("app.js", "original\n")
        self.start()
        self.write("app.js", "experiment\n", bump=False)
        self.hook_write("app.js")
        self.write("app.js", "original\n")
        self.hook_write("app.js")
        self.settle()
        self.assertIn("quietly undid the change to app.js", self.transcript)

    def test_silent_undo_is_announced(self):
        self.write("app.js", "original\n")
        self.start()
        self.write("app.js", "experiment\n")
        self.tick()
        self.write("app.js", "original\n")
        self.settle()
        self.assertIn("quietly undid the change to app.js", self.transcript)

    def test_return_to_session_start_version_is_flagged(self):
        self.write("cfg.json", "{}\n")
        self.start()
        self.write("cfg.json", "{\"a\": 1}\n")
        self.settle()
        self.write("cfg.json", "{}\n")
        self.settle()
        self.assertIn("put cfg.json back to the version it had when we started",
                      self.transcript)

    def test_passing_check_is_announced(self):
        self.start()
        self.write("x.py", "pass\n")
        self.tick()
        self.check(0, "pytest")
        self.tick()
        self.assertIn("then 'pytest' passed", self.transcript)

    def test_failing_check_reports_the_real_exit_code(self):
        self.start()
        self.check(3, "npm run build")
        self.tick()
        self.assertIn("then 'npm run build' FAILED (exit code 3)", self.transcript)

    def test_check_with_no_file_changes_still_reports(self):
        self.start()
        self.check(0, "pytest")
        self.tick()
        self.assertIn("passed", self.transcript)

    def test_pause_with_no_changes_says_nothing(self):
        self.start()
        self.w.last_change_at = time.time() - 1000
        self.tick()
        self.assertEqual(self.said, [])

    def test_results_tray_accepts_plain_lines(self):
        self.start()
        self.tray("PASS npm test")
        self.tick()
        self.assertIn("then 'npm test' passed", self.transcript)
        self.tray("1 pytest")
        self.tick()
        self.assertIn("then 'pytest' FAILED (exit code 1)", self.transcript)

    def test_stuck_after_three_failing_attempts_on_the_same_file(self):
        self.start()
        for i in range(3):
            self.write("login.js", "attempt %d\n" % i)
            self.tick()
            self.check(1, "npm test")
            self.tick()
        self.assertIn("the agent has changed login.js in 3 attempts", self.transcript)

    def test_stuck_is_announced_only_once(self):
        self.start()
        for i in range(5):
            self.write("login.js", "attempt %d\n" % i)
            self.tick()
            self.check(1, "npm test")
            self.tick()
        self.assertEqual(self.transcript.count("attempts that each ended"), 1)

    def test_pull_state_summary(self):
        self.start()
        self.write("new1.py", "x\n")
        self.write("new2.py", "y\n")
        self.settle()
        self.said = []
        self.w.state_summary()
        self.assertIn("2 new files", self.transcript)

    def test_pull_state_summary_when_nothing_changed(self):
        self.start()
        self.w.state_summary()
        self.assertIn("nothing has changed since we started", self.transcript)


# ==========================================================================
# REGRESSION TESTS - the defects found in review, by what Willow used to say
# ==========================================================================

class TestRegressions(WillowCase):

    # -- C1: a command that cannot be launched is not a test failure -------

    def test_C1_unlaunchable_command_is_not_reported_as_a_failure(self):
        os.makedirs(os.path.join(self.dir, W.WILLOW_DIR), exist_ok=True)
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = W.cmd_run(["definitely-not-a-real-command-xyz", "test"])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 127)
        self.assertIn("wasn't found", err.getvalue())
        self.assertIn("not a test failure", err.getvalue())

        # ...and the watcher must not turn it into a verdict
        self.start()
        self.tick()
        self.assertNotIn("FAILED", self.transcript)
        self.assertNotIn("passed", self.transcript)
        self.assertIn("couldn't run", self.transcript)

    def test_C1_real_command_still_reports_its_exit_code(self):
        os.makedirs(os.path.join(self.dir, W.WILLOW_DIR), exist_ok=True)
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                ok = W.cmd_run(["python", "-c", "raise SystemExit(0)"])
                bad = W.cmd_run(["python", "-c", "raise SystemExit(4)"])
        finally:
            os.chdir(cwd)
        self.assertEqual(ok, 0)
        self.assertEqual(bad, 4)

    # -- C2: a failure must never be dropped ------------------------------

    def test_C2_failing_check_survives_a_pass_in_the_same_tick(self):
        self.start()
        self.check(1, "npm run lint")
        self.check(0, "npm test")
        self.tick()
        self.assertIn("'npm run lint' FAILED", self.transcript)
        self.assertIn("'npm test' passed", self.transcript)

    def test_C2_three_results_in_one_tick_are_all_announced(self):
        self.start()
        self.check(0, "tsc")
        self.check(1, "eslint")
        self.check(0, "jest")
        self.tick()
        for expected in ("'tsc' passed", "'eslint' FAILED", "'jest' passed"):
            self.assertIn(expected, self.transcript)

    # -- H1: a locked file is not a silent undo ---------------------------

    def test_H1_briefly_unreadable_file_produces_no_announcement(self):
        self.write("app.js", "hello\n")
        self.start()
        real_open = open
        state = {"blocked": True}

        def flaky_open(path, *a, **k):
            mode = a[0] if a else k.get("mode", "r")
            if str(path).endswith("app.js") and "b" in mode and state["blocked"]:
                raise OSError(13, "locked by another process")
            return real_open(path, *a, **k)

        import builtins
        builtins.open = flaky_open
        try:
            self.tick()
        finally:
            builtins.open = real_open
        state["blocked"] = False
        self.settle()
        self.assertNotIn("quietly undid", self.transcript)
        self.assertNotIn("deleted app.js", self.transcript)

    def test_H1_file_that_really_is_gone_is_still_reported(self):
        self.write("real.js", "content\n")
        self.start()
        self.remove("real.js")
        self.settle()
        self.assertIn("deleted real.js", self.transcript)

    # -- H2: stuck must be attributable and actually consecutive ----------

    def test_H2_stuck_is_not_claimed_for_an_unrelated_file(self):
        """A README edited while some other suite is red is not 'stuck'...
        but it IS the file that changed, so the wording must be exact."""
        self.start()
        for i in range(3):
            self.write("README.md", "docs %d\n" % i)
            self.tick()
            self.check(1, "npm test")
            self.tick()
        # The old wording asserted the agent was stuck ON the file. The new
        # wording states only what was observed: this file changed in three
        # attempts that each ended with this command failing.
        self.assertNotIn("seems stuck on", self.transcript)
        self.assertIn("has changed README.md in 3 attempts that each ended "
                      "with 'npm test' failing", self.transcript)

    def test_H2_a_passing_check_resets_the_streak(self):
        self.start()
        for i in range(2):
            self.write("a.js", "v%d\n" % i)
            self.tick()
            self.check(1, "npm test")
            self.tick()
        self.write("b.js", "ok\n")
        self.tick()
        self.check(0, "npm test")          # a pass for the same command
        self.tick()
        self.write("a.js", "v9\n")
        self.tick()
        self.check(1, "npm test")
        self.tick()
        self.assertNotIn("attempts that each ended", self.transcript)
        self.assertEqual(self.w.fail_streak.get(("npm test", "a.js")), 1)

    def test_H2_streaks_are_kept_per_command(self):
        self.start()
        for i in range(2):
            self.write("a.js", "v%d\n" % i)
            self.tick()
            self.check(1, "npm test")
            self.tick()
        self.write("a.js", "v9\n")
        self.tick()
        self.check(1, "cargo build")       # a different command failing
        self.tick()
        self.assertNotIn("attempts that each ended", self.transcript)

    # -- H3: a rewritten results file must not deafen Willow --------------

    def test_H3_rewritten_results_file_is_still_read(self):
        self.start()
        self.tray("PASS suite-one")
        self.tick()
        self.tray("FAIL npm test", rewrite=True)
        self.tick()
        self.assertIn("'npm test' FAILED", self.transcript)

    def test_H3_rewritten_inbox_is_still_read(self):
        self.start()
        self.check(0, "first")
        self.tick()
        with open(self.w.inbox_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "check", "command": "second",
                                "exit_code": 1}) + "\n")
        self.tick()
        self.assertIn("'second' FAILED", self.transcript)

    def test_H3_appending_normally_does_not_replay_old_results(self):
        self.start()
        self.check(0, "first")
        self.tick()
        self.check(0, "second")
        self.tick()
        self.assertEqual(self.transcript.count("'first' passed"), 1)

    # -- H4: results must not vanish into an unwatched folder -------------

    def test_H4_run_warns_when_nobody_is_watching(self):
        os.makedirs(os.path.join(self.dir, W.WILLOW_DIR), exist_ok=True)
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                W.cmd_run(["python", "-c", "pass"])
        finally:
            os.chdir(cwd)
        self.assertIn("no Willow is watching this folder", err.getvalue())

    def test_H4_no_warning_when_a_watcher_is_live(self):
        wd = os.path.join(self.dir, W.WILLOW_DIR)
        os.makedirs(wd, exist_ok=True)
        with open(os.path.join(wd, W.WATCHING_NAME), "w", encoding="utf-8") as f:
            json.dump({"folder": self.dir, "pid": 1, "t": time.time()}, f)
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                W.cmd_run(["python", "-c", "pass"])
        finally:
            os.chdir(cwd)
        self.assertNotIn("no Willow is watching", err.getvalue())

    def test_H4_a_stale_watcher_file_counts_as_nobody_watching(self):
        wd = os.path.join(self.dir, W.WILLOW_DIR)
        os.makedirs(wd, exist_ok=True)
        with open(os.path.join(wd, W.WATCHING_NAME), "w", encoding="utf-8") as f:
            json.dump({"folder": self.dir, "pid": 1,
                       "t": time.time() - 600}, f)
        live, _ = W.watcher_status(wd)
        self.assertFalse(live)

    # -- M3: deleting a file it created is a deletion ---------------------

    def test_M3_create_then_delete_is_reported_as_a_deletion(self):
        self.start()
        self.write("temp.js", "scratch\n")
        self.settle()
        self.remove("temp.js")
        self.settle()
        self.assertIn("deleted temp.js, which it created earlier", self.transcript)
        self.assertNotIn("back to its earlier version", self.transcript)

    # -- M4: renames --------------------------------------------------------

    def test_M4_rename_is_reported_as_a_rename(self):
        self.write("old.js", "body of the file\n")
        self.start()
        os.rename(os.path.join(self.dir, "old.js"),
                  os.path.join(self.dir, "new.js"))
        self.settle()
        self.assertIn("renamed old.js to new.js", self.transcript)
        self.assertNotIn("2 files:", self.transcript)

    def test_M4_unrelated_create_and_delete_is_not_a_rename(self):
        self.write("old.js", "one thing\n")
        self.start()
        self.remove("old.js")
        self.write("new.js", "something completely different\n")
        self.settle()
        self.assertNotIn("renamed", self.transcript)

    # -- misc --------------------------------------------------------------

    def test_notices_are_relayed_without_becoming_a_verdict(self):
        self.start()
        with open(self.w.inbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "notice",
                                "message": "Claude is waiting for permission "
                                           "to run npm install"}) + "\n")
        self.tick()
        self.assertIn("waiting for permission", self.transcript)
        self.assertNotIn("FAILED", self.transcript)
        self.assertNotIn("passed", self.transcript)

    def test_check_duration_is_announced(self):
        self.start()
        self.check(0, "pytest", seconds=4.2)
        self.tick()
        self.assertIn("passed in 4.2 seconds", self.transcript)

    def test_failure_detail_is_announced(self):
        self.start()
        self.check(1, "pytest", detail="3 failed, 12 passed. "
                                       "First failure: tests/test_auth.py")
        self.tick()
        self.assertIn("First failure: tests/test_auth.py", self.transcript)

    def test_a_windows_negative_exit_code_is_spoken_readably(self):
        self.start()
        self.check(4294963238, "npm test")
        self.tick()
        self.assertIn("exit code -4058", self.transcript)
        self.assertNotIn("4294963238", self.transcript)

    def test_ignored_folders_are_not_narrated(self):
        self.start()
        self.write(os.path.join("node_modules", "junk.js"), "noise\n")
        self.write(os.path.join(".git", "COMMIT_EDITMSG"), "wip\n")
        self.settle()
        self.assertEqual(self.said, [])


# ==========================================================================
# The faithfulness check - what makes any model safe to narrate with
# ==========================================================================

class TestFaithfulness(unittest.TestCase):

    FACTS = "edited app.py (+4 -1 lines). then 'pytest' passed."

    def test_a_plain_rewording_is_accepted(self):
        self.assertTrue(W.faithful(
            self.FACTS,
            "It changed app.py, adding 4 lines and removing 1, and then pytest passed."))

    def test_dropping_detail_is_allowed(self):
        self.assertTrue(W.faithful(self.FACTS, "It edited app.py and pytest passed."))

    def test_an_invented_pass_is_rejected(self):
        self.assertFalse(W.faithful("edited app.py (+4 -1 lines).",
                                    "It edited app.py and the tests passed."))

    def test_a_flipped_verdict_is_rejected(self):
        self.assertFalse(W.faithful("then 'pytest' FAILED (exit code 1).",
                                    "pytest passed."))

    def test_an_invented_number_is_rejected(self):
        self.assertFalse(W.faithful(self.FACTS,
                                    "It edited app.py, adding 40 lines."))

    def test_a_number_written_as_a_word_is_still_checked(self):
        self.assertFalse(W.faithful("edited app.py (+4 lines).",
                                    "It added twelve lines to app.py."))
        self.assertTrue(W.faithful("edited app.py (+4 lines).",
                                   "It added four lines to app.py."))

    def test_an_invented_filename_is_rejected(self):
        self.assertFalse(W.faithful(self.FACTS,
                                    "It edited app.py and config.yaml."))

    def test_empty_output_is_rejected(self):
        self.assertFalse(W.faithful(self.FACTS, ""))
        self.assertFalse(W.faithful(self.FACTS, "   "))

    def test_narrator_discards_an_unfaithful_answer(self):
        class LyingProvider(W.Provider):
            def available(self):
                return True

            def text(self, system, user, max_tokens, timeout):
                return "All 99 tests passed, nice work on server.py!"

        n = W.Narrator(LyingProvider())
        self.assertIsNone(n.narrate("edited app.py (+4 -1 lines)."))
        self.assertEqual(n.rejected, 1)

    def test_narrator_keeps_a_faithful_answer(self):
        class HonestProvider(W.Provider):
            def available(self):
                return True

            def text(self, system, user, max_tokens, timeout):
                return "It made a small edit to app.py."

        n = W.Narrator(HonestProvider())
        self.assertEqual(n.narrate("edited app.py (+4 -1 lines)."),
                         "It made a small edit to app.py.")

    def test_narrator_falls_back_when_the_provider_fails(self):
        class DeadProvider(W.Provider):
            def available(self):
                return True

            def text(self, system, user, max_tokens, timeout):
                return None

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(W.Narrator(DeadProvider()).narrate("created a.py."))


# ==========================================================================
# Providers - Anthropic, OpenAI, Ollama, anything OpenAI-compatible
# ==========================================================================

class TestProviders(unittest.TestCase):

    def setUp(self):
        self.calls = []
        self._post, self._get = W._post_json, W._get_json
        self.addCleanup(self._restore)

    def _restore(self):
        W._post_json, W._get_json = self._post, self._get

    def fake_post(self, response):
        def _post(url, headers, payload, timeout):
            self.calls.append({"url": url, "headers": headers,
                               "payload": payload, "timeout": timeout})
            return response
        W._post_json = _post

    def fake_get(self, response):
        def _get(url, timeout, headers=None):
            self.calls.append({"url": url})
            return response
        W._get_json = _get

    # -- Anthropic --------------------------------------------------------

    def test_anthropic_text(self):
        self.fake_post({"content": [{"type": "text", "text": "Made a file."}]})
        p = W.AnthropicProvider(api_key="k")
        self.assertEqual(p.text("sys", "facts", 100, 5), "Made a file.")
        call = self.calls[0]
        self.assertTrue(call["url"].endswith("/v1/messages"))
        self.assertEqual(call["headers"]["x-api-key"], "k")

    def test_anthropic_refusal_falls_back(self):
        self.fake_post({"stop_reason": "refusal", "content": []})
        self.assertIsNone(W.AnthropicProvider(api_key="k").text("s", "f", 10, 5))

    # -- OpenAI and compatibles -------------------------------------------

    def test_openai_text(self):
        self.fake_post({"choices": [{"message": {"content": "Made a file."}}]})
        p = W.OpenAICompatProvider(api_key="sk-test")
        self.assertEqual(p.text("sys", "facts", 100, 5), "Made a file.")
        call = self.calls[0]
        self.assertTrue(call["url"].endswith("/chat/completions"))
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-test")

    def test_openai_vision_sends_a_data_uri(self):
        self.fake_post({"choices": [{"message": {"content": "A login form."}}]})
        p = W.OpenAICompatProvider(api_key="sk-test")
        self.assertEqual(p.vision("sys", "look", b"\x89PNG-fake", 100, 5),
                         "A login form.")
        content = self.calls[0]["payload"]["messages"][1]["content"]
        url = [c for c in content if c["type"] == "image_url"][0]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))

    def test_a_custom_base_url_is_used_verbatim(self):
        self.fake_post({"choices": [{"message": {"content": "ok"}}]})
        p = W.OpenAICompatProvider(base_url="http://192.168.1.9:8000/v1",
                                   require_key=False)
        p.text("s", "f", 10, 5)
        self.assertEqual(self.calls[0]["url"],
                         "http://192.168.1.9:8000/v1/chat/completions")
        self.assertNotIn("Authorization", self.calls[0]["headers"])

    # -- Ollama: a model on this machine ----------------------------------

    def test_ollama_is_available_when_models_are_installed(self):
        self.fake_get({"models": [{"name": "llama3.2:3b"}, {"name": "llava:7b"}]})
        p = W.OllamaProvider()
        self.assertTrue(p.available())
        self.assertIsNone(p.problem())

    def test_ollama_needs_no_api_key(self):
        self.fake_get({"models": [{"name": "llama3.2:3b"}]})
        p = W.OllamaProvider()
        self.assertEqual(p.api_key, "")
        self.assertTrue(p.available())

    def test_ollama_picks_a_small_text_model_by_itself(self):
        self.fake_get({"models": [{"name": "llama3.1:70b"},
                                  {"name": "llama3.2:3b"},
                                  {"name": "llava:7b"}]})
        self.assertEqual(W.OllamaProvider().pick_text(), "llama3.2:3b")

    def test_gemma4_is_preferred_when_it_is_installed(self):
        self.fake_get({"models": [{"name": "llama3.2:3b"},
                                  {"name": "gemma4:e4b"},
                                  {"name": "mistral:latest"}]})
        self.assertEqual(W.OllamaProvider().pick_text(), "gemma4:e4b")

    def test_the_plain_gemma4_tag_is_recognised(self):
        """`ollama pull gemma4` installs it as gemma4:latest."""
        self.fake_get({"models": [{"name": "gemma4:latest"},
                                  {"name": "llama3.2:3b"}]})
        p = W.OllamaProvider()
        self.assertEqual(p.pick_text(), "gemma4:latest")
        self.assertEqual(p.pick_vision(), "gemma4:latest")

    def test_the_smallest_gemma4_wins_for_narration(self):
        """Narration is a rewording job on a 1.5 s budget - big is not better."""
        self.fake_get({"models": [{"name": "gemma4:31b"}, {"name": "gemma4:26b"},
                                  {"name": "gemma4:e4b"}, {"name": "gemma4:e2b"}]})
        self.assertEqual(W.OllamaProvider().pick_text(), "gemma4:e2b")

    def test_gemma4_covers_describe_too_so_only_one_model_loads(self):
        """Every Gemma 4 variant takes images, so reuse the text model rather
        than making Ollama evict it to load a separate vision model."""
        self.fake_get({"models": [{"name": "gemma4:e4b"}, {"name": "llava:13b"}]})
        p = W.OllamaProvider()
        self.assertTrue(p.supports_vision())
        self.assertEqual(p.pick_vision(), "gemma4:e4b")
        self.assertEqual(p.pick_vision(), p.pick_text())

    def test_ollama_picks_a_vision_model_by_itself(self):
        self.fake_get({"models": [{"name": "llama3.2:3b"}, {"name": "llava:13b"}]})
        p = W.OllamaProvider()
        self.assertEqual(p.pick_vision(), "llava:13b")
        self.assertTrue(p.supports_vision())

    def test_ollama_says_plainly_when_no_vision_model_is_installed(self):
        self.fake_get({"models": [{"name": "llama3.2:3b"}]})
        p = W.OllamaProvider()
        self.assertFalse(p.supports_vision())
        self.assertIn("ollama pull gemma4", p.vision_problem())

    def test_setup_advice_names_the_recommended_model(self):
        def boom(url, timeout, headers=None):
            raise OSError("connection refused")
        W._get_json = boom
        self.assertIn("ollama pull gemma4", W.OllamaProvider().problem())

    def test_ollama_running_with_no_models_is_not_called_unreachable(self):
        """It IS answering - saying otherwise sends the user to reinstall
        something that is already working."""
        self.fake_get({"models": []})
        p = W.OllamaProvider()
        self.assertTrue(p.reachable())
        self.assertFalse(p.available())
        self.assertIn("no models are installed", p.problem())
        self.assertNotIn("isn't answering", p.problem())

    def test_ollama_not_running_says_so(self):
        def boom(url, timeout, headers=None):
            raise OSError("connection refused")
        W._get_json = boom
        p = W.OllamaProvider()
        self.assertFalse(p.reachable())
        self.assertIn("isn't answering", p.problem())

    def test_an_explicit_model_still_overrides_the_preference(self):
        self.fake_get({"models": [{"name": "gemma4:e4b"}, {"name": "phi3"}]})
        p = W.OllamaProvider(text_model="phi3", vision_model="llava:34b")
        self.assertEqual(p.pick_text(), "phi3")
        self.assertEqual(p.pick_vision(), "llava:34b")

    def test_ollama_explains_itself_when_not_running(self):
        def boom(url, timeout, headers=None):
            raise OSError("connection refused")
        W._get_json = boom
        p = W.OllamaProvider()
        self.assertFalse(p.available())
        self.assertIn("ollama.com", p.problem())

    def test_ollama_text_goes_through_the_openai_endpoint(self):
        self.fake_get({"models": [{"name": "llama3.2:3b"}]})
        self.fake_post({"choices": [{"message": {"content": "Made a file."}}]})
        p = W.OllamaProvider()
        p._installed = ["llama3.2:3b"]
        self.assertEqual(p.text("sys", "facts", 100, 5), "Made a file.")
        self.assertEqual(self.calls[-1]["url"],
                         "http://localhost:11434/v1/chat/completions")

    def test_ollama_vision_uses_the_native_images_field(self):
        self.fake_post({"message": {"content": "A dark dashboard."}})
        p = W.OllamaProvider()
        p._installed = ["llava:7b"]
        self.assertEqual(p.vision("sys", "look", b"PNGDATA", 100, 5),
                         "A dark dashboard.")
        call = self.calls[-1]
        self.assertEqual(call["url"], "http://localhost:11434/api/chat")
        self.assertIn("images", call["payload"]["messages"][1])

    def test_ollama_host_can_be_pointed_elsewhere(self):
        self.fake_get({"models": [{"name": "qwen2.5:3b"}]})
        p = W.OllamaProvider(base_url="http://gpu-box:11434/v1")
        self.assertEqual(p.host, "http://gpu-box:11434")
        self.assertEqual(p.base_url, "http://gpu-box:11434/v1")

    # -- choosing between them --------------------------------------------

    def test_explicit_choice_wins(self):
        self.fake_get({"models": [{"name": "llama3.2:3b"}]})
        p = W.resolve_provider(dict(W.DEFAULTS, provider="ollama"))
        self.assertIsInstance(p, W.OllamaProvider)

    def test_provider_none_disables_models_entirely(self):
        p = W.resolve_provider(dict(W.DEFAULTS, provider="none"))
        self.assertFalse(p.available())

    def test_auto_falls_back_to_a_local_model_when_no_key_is_set(self):
        self.fake_get({"models": [{"name": "llama3.2:3b"}]})
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "WILLOW_API_KEY"):
            os.environ.pop(var, None)
        real_read = W.read_key_file
        W.read_key_file = lambda name: None
        try:
            p = W.resolve_provider(dict(W.DEFAULTS, provider="auto"))
        finally:
            W.read_key_file = real_read
        self.assertIsInstance(p, W.OllamaProvider)

    def test_auto_prefers_a_configured_key_over_a_local_model(self):
        self.fake_get({"models": [{"name": "llama3.2:3b"}]})
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            p = W.resolve_provider(dict(W.DEFAULTS, provider="auto"))
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        self.assertIsInstance(p, W.AnthropicProvider)


# ==========================================================================
# A local model, over real HTTP
# ==========================================================================

class TestLocalModelOverHTTP(unittest.TestCase):
    """Mocking `_post_json` proves Willow calls something. This runs a real
    server speaking Ollama's actual protocol, so it proves Willow sends
    requests a real `ollama serve` would accept."""

    @classmethod
    def setUpClass(cls):
        import threading as th
        from http.server import BaseHTTPRequestHandler, HTTPServer
        cls.received = []
        cls.models = [{"name": "llama3.2:3b"}, {"name": "llava:7b"},
                      {"name": "llama3.1:70b"}]
        cls.reply = {"text": "It made a small change to app.py."}
        outer = cls

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.received.append({"method": "GET", "path": self.path})
                if self.path == "/api/tags":
                    return self._send({"models": outer.models})
                self._send({"error": "not found"}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n) or b"{}")
                outer.received.append({"method": "POST", "path": self.path,
                                       "payload": payload,
                                       "headers": dict(self.headers)})
                if self.path == "/v1/chat/completions":
                    return self._send(
                        {"choices": [{"message": {"content": outer.reply["text"]}}]})
                if self.path == "/api/chat":
                    return self._send(
                        {"message": {"content": "A login form on a dark background."}})
                self._send({"error": "not found"}, 404)

        cls.srv = HTTPServer(("127.0.0.1", 0), Handler)
        cls.host = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        th.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        type(self).received = []
        self.__class__.received = []
        type(self).models[:] = [{"name": "llama3.2:3b"}, {"name": "llava:7b"},
                                {"name": "llama3.1:70b"}]
        type(self).reply["text"] = "It made a small change to app.py."

    def posts(self):
        return [r for r in type(self).received if r["method"] == "POST"]

    def test_a_local_model_is_found_and_needs_no_key(self):
        p = W.OllamaProvider(base_url=self.host)
        self.assertTrue(p.available())
        self.assertEqual(p.api_key, "")
        self.assertIsNone(p.problem())

    def test_it_chooses_sensible_models_by_itself(self):
        p = W.OllamaProvider(base_url=self.host)
        self.assertEqual(p.pick_text(), "llama3.2:3b")     # not the 70b
        self.assertEqual(p.pick_vision(), "llava:7b")

    def test_a_single_gemma4_pull_serves_both_channels(self):
        """The recommended setup, end to end over HTTP: one model installed,
        used for narration and for describe."""
        type(self).models[:] = [{"name": "gemma4:latest"}]
        p = W.OllamaProvider(base_url=self.host)
        self.assertIsNone(p.problem())
        self.assertEqual(p.pick_text(), "gemma4:latest")
        self.assertTrue(p.supports_vision())

        out = W.Narrator(p, timeout=5).narrate("edited app.py (+4 -1 lines).")
        self.assertEqual(out, type(self).reply["text"])
        self.assertEqual(self.posts()[-1]["payload"]["model"], "gemma4:latest")

        desc = p.vision("sys", "look", b"\x89PNG-fake", 300, 10)
        self.assertEqual(desc, "A login form on a dark background.")
        self.assertEqual(self.posts()[-1]["path"], "/api/chat")
        self.assertEqual(self.posts()[-1]["payload"]["model"], "gemma4:latest")

    def test_narration_sends_only_the_verified_facts(self):
        p = W.OllamaProvider(base_url=self.host)
        facts = "edited app.py (+4 -1 lines)."
        out = W.Narrator(p, timeout=5).narrate(facts)
        sent = self.posts()[-1]
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(sent["payload"]["model"], "llama3.2:3b")
        self.assertEqual(sent["payload"]["messages"][1]["content"], facts)
        self.assertNotIn("Authorization", sent["headers"])
        self.assertEqual(out, type(self).reply["text"])

    def test_an_unfaithful_local_model_is_overruled(self):
        type(self).reply["text"] = "All 42 tests passed and I fixed config.yaml!"
        p = W.OllamaProvider(base_url=self.host)
        n = W.Narrator(p, timeout=5)
        self.assertIsNone(n.narrate("edited app.py (+4 -1 lines)."))
        self.assertEqual(n.rejected, 1)

    def test_vision_uses_the_native_image_endpoint(self):
        p = W.OllamaProvider(base_url=self.host)
        desc = p.vision("sys", "look", b"\x89PNG-fake", 300, 10)
        sent = self.posts()[-1]
        self.assertEqual(sent["path"], "/api/chat")
        self.assertEqual(sent["payload"]["model"], "llava:7b")
        import base64 as b64
        self.assertEqual(
            b64.standard_b64decode(sent["payload"]["messages"][1]["images"][0]),
            b"\x89PNG-fake")
        self.assertEqual(desc, "A login form on a dark background.")

    def test_no_vision_model_is_explained_not_guessed(self):
        type(self).models[:] = [{"name": "llama3.2:3b"}]
        p = W.OllamaProvider(base_url=self.host)
        self.assertFalse(p.supports_vision())
        lines = W.Viewer(p).describe("proj", "nowhere.png")
        self.assertEqual(len(lines), 1)
        self.assertIn("ollama pull", lines[0])

    def test_an_absent_ollama_gives_instructions(self):
        p = W.OllamaProvider(base_url="http://127.0.0.1:1")
        self.assertFalse(p.available())
        self.assertIn("ollama.com", p.problem())

    def test_any_openai_compatible_server_works_the_same_way(self):
        p = W.OpenAICompatProvider(base_url=self.host + "/v1", api_key="",
                                   text_model="local-model", require_key=False)
        self.assertEqual(p.text("sys", "facts", 100, 5),
                         type(self).reply["text"])
        self.assertEqual(self.posts()[-1]["payload"]["model"], "local-model")


# ==========================================================================
# Reading a failure back to the user
# ==========================================================================

class TestOutputSummary(unittest.TestCase):

    def test_pytest_failure(self):
        out = ("collected 15 items\n"
               "FAILED tests/test_auth.py::test_login_redirect - AssertionError\n"
               "=================== 3 failed, 12 passed in 4.21s ===================\n")
        s = W.summarize_output(out, 1)
        self.assertIn("3 failed, 12 passed", s)
        self.assertIn("tests/test_auth.py::test_login_redirect", s)

    def test_pytest_success(self):
        s = W.summarize_output("======== 12 passed in 1.02s ========\n", 0)
        self.assertIn("12 passed", s)

    def test_pytest_quiet_mode_still_names_the_failing_test(self):
        """-q drops the ==== decoration, which used to fall through to the
        generic rule and lose the failing test's name."""
        out = (".F                                              [100%]\r\n"
               "=================== FAILURES ===================\r\n"
               "___________________ test_bad ___________________\r\n"
               "=========== short test summary info ============\r\n"
               "FAILED test_thing.py::test_bad - assert 2 == 3\r\n"
               "1 failed, 1 passed in 0.09s\r\n")
        s = W.summarize_output(out, 1)
        self.assertIn("1 failed, 1 passed", s)
        self.assertIn("test_thing.py::test_bad", s)

    def test_a_decoration_line_without_counts_is_not_a_summary(self):
        out = "================= FAILURES =================\nboom\n"
        self.assertNotIn("FAILURES", W.summarize_output(out, 1) or "")

    def test_jest_failure(self):
        out = ("  ● renders the header\n"
               "Tests:       2 failed, 40 passed, 42 total\n")
        s = W.summarize_output(out, 1)
        self.assertIn("2 failed, 40 passed", s)
        self.assertIn("renders the header", s)

    def test_go_test_failure(self):
        s = W.summarize_output("--- FAIL: TestLoginRedirect (0.00s)\nFAIL\n", 1)
        self.assertIn("TestLoginRedirect", s)

    def test_typescript_error(self):
        out = "src/app.ts(42,17): error TS2345: Argument of type 'string'.\n"
        s = W.summarize_output(out, 2)
        self.assertIn("src/app.ts(42,17)", s)
        self.assertIn("TS2345", s)

    def test_ansi_colour_codes_are_stripped(self):
        out = "\x1b[31m======== 1 failed in 0.5s ========\x1b[0m\n"
        self.assertNotIn("\x1b", W.summarize_output(out, 1))

    def test_npm_error_skips_the_log_file_path(self):
        out = ("npm error code ENOENT\n"
               "npm error syscall open\n"
               "npm error path C:\\proj\\package.json\n"
               "npm error errno -4058\n"
               "npm error enoent Could not read package.json: Error: ENOENT\n"
               "npm error A complete log of this run can be found in: "
               "C:\\Users\\x\\npm-cache\\_logs\\2026-01-01-debug-0.log\n")
        s = W.summarize_output(out, -4058)
        self.assertIn("Could not read package.json", s)
        self.assertNotIn("A complete log", s)
        self.assertNotIn("_logs", s)

    def test_old_style_npm_errors_still_work(self):
        out = "npm ERR! missing script: test\n"
        self.assertIn("missing script: test", W.summarize_output(out, 1))

    def test_windows_negative_exit_codes_are_readable(self):
        self.assertEqual(W.normalize_exit_code(4294963238), -4058)
        self.assertEqual(W.normalize_exit_code(1), 1)
        self.assertEqual(W.normalize_exit_code(0), 0)
        self.assertEqual(W.normalize_exit_code(127), 127)

    def test_unknown_runner_quotes_its_last_line(self):
        s = W.summarize_output("doing things\nsomething went wrong\n", 1)
        self.assertEqual(s, "something went wrong")

    def test_no_output_gives_nothing(self):
        self.assertIsNone(W.summarize_output("", 1))


# ==========================================================================
# Speech, configuration and setup
# ==========================================================================

class TestSpeakable(unittest.TestCase):

    def test_plus_and_minus_are_spoken_as_words(self):
        self.assertIn("plus 12, minus 3 lines",
                      W.speakable("edited a.js (+12 -3 lines)."))
        self.assertIn("plus 4 lines", W.speakable("edited a.js (+4 lines)."))

    def test_windows_paths_are_readable(self):
        self.assertIn("src/app.js", W.speakable("edited src\\app.js"))

    def test_non_ascii_filenames_survive(self):
        for name in ("café-menü.tsx", "テスト.py", "naïve.js"):
            self.assertIn(name, W.speakable("created " + name + "."))


class TestSpeaker(unittest.TestCase):
    """Queue behaviour, and the two ways speech used to go silently missing."""

    def make(self):
        s = W.Speaker(enabled=True, rate=3, use_host=False)
        s._stop.set()                      # don't actually speak in the suite
        self.addCleanup(s.close)
        return s

    def test_say_never_blocks_the_caller(self):
        s = self.make()
        t0 = time.time()
        for i in range(20):
            s.say("announcement %d" % i)
        self.assertLess(time.time() - t0, 0.2)

    def test_failures_jump_ahead_of_routine_announcements(self):
        s = self.make()
        s.say("edited a.js", priority=W.P_NORMAL)
        s.say("npm test FAILED", priority=W.P_URGENT)
        first = s.q.get_nowait()
        self.assertEqual(first[0], W.P_URGENT)

    def test_a_backlog_of_routine_announcements_is_trimmed(self):
        s = self.make()
        for i in range(20):
            s.say("edited file%d.js" % i, priority=W.P_NORMAL)
        self.assertLessEqual(s.q.qsize(), W.MAX_PENDING_NORMAL + 1)

    def test_urgent_announcements_are_never_dropped(self):
        s = self.make()
        for i in range(20):
            s.say("FAILURE %d" % i, priority=W.P_URGENT)
        self.assertEqual(s.q.qsize(), 20)

    def test_stop_clears_the_backlog(self):
        s = self.make()
        for i in range(5):
            s.say("thing %d" % i)
        s.stop_talking()
        self.assertEqual(s.q.qsize(), 0)

    def test_rate_stays_in_range(self):
        s = self.make()
        self.assertEqual(s.set_rate(99), 10)
        self.assertEqual(s.set_rate(-99), -10)

    def test_the_one_shot_script_carries_no_inline_base64(self):
        """Regression: base64 on a PowerShell command line is the classic
        obfuscated-malware shape, so AMSI kills the process instantly and the
        user hears nothing at all. The text goes in the environment instead."""
        import inspect
        src = inspect.getsource(W.Speaker._speak_windows_oneshot)
        self.assertNotIn("FromBase64String", src)
        self.assertIn("WILLOW_SPEAK_TEXT", src)

    def test_the_voice_host_is_not_trusted_until_it_answers(self):
        """Regression: a host that dies silently must never swallow speech."""
        self.assertIn("WILLOW-READY", W.Speaker.HOST_SCRIPT)
        import inspect
        src = inspect.getsource(W.Speaker._ensure_host)
        self.assertIn("_wait_for_ready", src)

    def test_the_host_never_sets_console_input_encoding(self):
        """Regression: setting it throws when stdin is a pipe, which killed
        the host. Not needed - the wire protocol is ASCII base64."""
        self.assertNotIn("InputEncoding", W.Speaker.HOST_SCRIPT)

    def test_disabled_speaker_still_reports_what_it_would_have_said(self):
        seen = []
        s = W.Speaker(enabled=False, on_spoken=lambda f, sp: seen.append((f, sp)))
        self.addCleanup(s.close)
        s.say("created a.py.")
        self.assertEqual(seen, [("created a.py.", "created a.py.")])


class TestConfig(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="willow-cfg-")
        os.makedirs(os.path.join(self.dir, W.WILLOW_DIR))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write_cfg(self, data):
        with open(os.path.join(self.dir, W.WILLOW_DIR, "config.json"), "w",
                  encoding="utf-8") as f:
            json.dump(data, f)

    def test_defaults_are_used_when_nothing_is_configured(self):
        cfg = W.load_config(self.dir)
        self.assertEqual(cfg["voice_rate"], W.DEFAULTS["voice_rate"])

    def test_project_config_overrides_defaults(self):
        self.write_cfg({"voice_rate": 8, "provider": "ollama"})
        cfg = W.load_config(self.dir)
        self.assertEqual(cfg["voice_rate"], 8)
        self.assertEqual(cfg["provider"], "ollama")

    def test_environment_overrides_the_config_file(self):
        self.write_cfg({"provider": "anthropic"})
        os.environ["WILLOW_PROVIDER"] = "ollama"
        try:
            self.assertEqual(W.load_config(self.dir)["provider"], "ollama")
        finally:
            os.environ.pop("WILLOW_PROVIDER")

    def test_command_line_overrides_everything(self):
        self.write_cfg({"provider": "anthropic"})
        os.environ["WILLOW_PROVIDER"] = "openai"
        try:
            cfg = W.load_config(self.dir, {"provider": "ollama"})
        finally:
            os.environ.pop("WILLOW_PROVIDER")
        self.assertEqual(cfg["provider"], "ollama")

    def test_unknown_keys_in_the_config_file_are_ignored(self):
        self.write_cfg({"voice_rate": 5, "nonsense": True})
        cfg = W.load_config(self.dir)
        self.assertNotIn("nonsense", cfg)
        self.assertEqual(cfg["voice_rate"], 5)

    def test_a_broken_config_file_does_not_stop_willow(self):
        with open(os.path.join(self.dir, W.WILLOW_DIR, "config.json"), "w") as f:
            f.write("{ this is not json")
        self.assertEqual(W.load_config(self.dir)["voice_rate"],
                         W.DEFAULTS["voice_rate"])


class TestInit(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="willow-init-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        # `init` speaks a confirmation; keep the suite silent and quick.
        self._speaker = W.Speaker
        W.Speaker = lambda *a, **k: SilentSpeaker()
        self._sleep = time.sleep
        time.sleep = lambda s: None
        self.addCleanup(self._restore)

    def _restore(self):
        W.Speaker = self._speaker
        time.sleep = self._sleep

    def run_init(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = W.cmd_init([self.dir] + list(args))
        return code, out.getvalue()

    def test_init_writes_claude_md_with_a_real_path(self):
        self.run_init("--no-hooks")
        with open(os.path.join(self.dir, "CLAUDE.md"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("willow", content.lower())
        self.assertNotIn("<PATH-TO>", content)
        self.assertIn("run <your command>", content)

    def test_init_adds_willow_to_gitignore(self):
        self.run_init("--no-hooks")
        with open(os.path.join(self.dir, ".gitignore"), encoding="utf-8") as f:
            self.assertIn(".willow/", f.read())

    def test_init_does_not_duplicate_a_gitignore_entry(self):
        with open(os.path.join(self.dir, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("node_modules/\n.willow/\n")
        self.run_init("--no-hooks")
        with open(os.path.join(self.dir, ".gitignore"), encoding="utf-8") as f:
            self.assertEqual(f.read().count(".willow/"), 1)

    def test_init_preserves_an_existing_claude_md(self):
        with open(os.path.join(self.dir, "CLAUDE.md"), "w", encoding="utf-8") as f:
            f.write("# My project notes\n\nUse tabs, not spaces.\n")
        self.run_init("--no-hooks")
        with open(os.path.join(self.dir, "CLAUDE.md"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Use tabs, not spaces.", content)
        self.assertIn("willow:start", content)

    def test_init_is_idempotent(self):
        self.run_init("--no-hooks")
        self.run_init("--no-hooks")
        with open(os.path.join(self.dir, "CLAUDE.md"), encoding="utf-8") as f:
            self.assertEqual(f.read().count("willow:start"), 1)

    def test_init_installs_hooks_without_clobbering_existing_settings(self):
        settings = os.path.join(self.dir, ".claude", "settings.json")
        os.makedirs(os.path.dirname(settings))
        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"model": "opus", "hooks": {"Stop": [
                {"hooks": [{"type": "command", "command": "echo mine"}]}]}}, f)
        self.run_init()
        with open(settings, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["model"], "opus")
        self.assertIn("echo mine", json.dumps(data["hooks"]["Stop"]))
        self.assertIn("willow", json.dumps(data["hooks"]).lower())

    def test_init_hooks_are_idempotent(self):
        self.run_init()
        self.run_init()
        with open(os.path.join(self.dir, ".claude", "settings.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data["hooks"]["Stop"]), 1)


class TestHooks(unittest.TestCase):
    """Hooks are run by the harness, so what they report is external fact -
    but an unknown must never become a verdict."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="willow-hook-")
        os.makedirs(os.path.join(self.dir, W.WILLOW_DIR))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def fire(self, event, payload):
        import sys
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                W.cmd_hook([event])
        finally:
            sys.stdin = old_stdin
        path = os.path.join(self.dir, W.WILLOW_DIR, W.INBOX_NAME)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def test_notification_becomes_a_spoken_notice(self):
        recs = self.fire("Notification", {
            "cwd": self.dir,
            "message": "Claude needs permission to run npm install"})
        self.assertEqual(recs[0]["type"], "notice")
        self.assertIn("npm install", recs[0]["message"])

    def test_stop_reports_the_turn_ending(self):
        recs = self.fire("Stop", {"cwd": self.dir})
        self.assertEqual(recs[0]["type"], "notice")
        self.assertIn("finished its turn", recs[0]["message"])

    def test_bash_with_a_real_exit_code_becomes_a_check(self):
        recs = self.fire("PostToolUse", {
            "cwd": self.dir, "tool_name": "Bash",
            "tool_input": {"command": "npm test"},
            "tool_response": {"exit_code": 1, "stdout": "",
                              "stderr": "--- FAIL: TestThing"}})
        self.assertEqual(recs[0]["type"], "check")
        self.assertEqual(recs[0]["exit_code"], 1)
        self.assertIn("TestThing", recs[0]["detail"])

    def test_an_edit_becomes_a_counted_write_not_a_verdict(self):
        recs = self.fire("PostToolUse", {
            "cwd": self.dir, "tool_name": "Edit",
            "tool_input": {"file_path": os.path.join(self.dir, "app.py")},
            "tool_response": {"success": True}})
        self.assertEqual(recs[0]["type"], "write")
        self.assertEqual(recs[0]["count"], 1)
        self.assertTrue(recs[0]["path"].endswith("app.py"))

    def test_a_multi_edit_counts_every_edit_in_it(self):
        recs = self.fire("PostToolUse", {
            "cwd": self.dir, "tool_name": "MultiEdit",
            "tool_input": {"file_path": os.path.join(self.dir, "app.py"),
                           "edits": [{}, {}, {}]},
            "tool_response": {"success": True}})
        self.assertEqual(recs[0]["count"], 3)

    def test_bash_without_an_exit_code_never_becomes_a_verdict(self):
        recs = self.fire("PostToolUse", {
            "cwd": self.dir, "tool_name": "Bash",
            "tool_input": {"command": "npm test"},
            "tool_response": {"stdout": "looked fine to me"}})
        self.assertEqual(recs[0]["type"], "notice")
        self.assertNotIn("exit_code", recs[0])
        self.assertIn("couldn't check", recs[0]["message"])

    def test_a_malformed_payload_is_survivable(self):
        import sys
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("not json at all")
        try:
            self.assertEqual(W.cmd_hook(["Stop"]), 0)
        finally:
            sys.stdin = old_stdin


class TestEyes(unittest.TestCase):
    """The two-phase scanner: cheap, and it never invents a change."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="willow-eyes-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, rel, content):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        self._t = getattr(self, "_t", time.time()) + 2
        os.utime(path, (self._t, self._t))
        return path

    def test_no_changes_means_no_changes(self):
        self.write("a.py", "x\n")
        eyes = W.Eyes(self.dir)
        eyes.prime()
        self.assertEqual(eyes.look(), ([], []))
        self.assertEqual(eyes.look(), ([], []))

    def test_a_same_length_edit_is_still_detected(self):
        self.write("a.py", "v0\n")
        eyes = W.Eyes(self.dir)
        eyes.prime()
        self.write("a.py", "v1\n")
        changed, _ = eyes.look()
        self.assertEqual(changed, ["a.py"])

    def test_touching_without_changing_content_is_not_a_change(self):
        path = self.write("a.py", "x\n")
        eyes = W.Eyes(self.dir)
        eyes.prime()
        os.utime(path, (time.time() + 50, time.time() + 50))
        changed, _ = eyes.look()
        self.assertEqual(changed, [])

    def test_ignored_directories_are_never_read(self):
        self.write(os.path.join("node_modules", "big.js"), "x" * 100)
        self.write("real.js", "y\n")
        eyes = W.Eyes(self.dir)
        eyes.prime()
        self.assertIn("real.js", eyes.hashes)
        self.assertEqual([k for k in eyes.hashes if "node_modules" in k], [])

    def test_deletion_is_reported_once(self):
        self.write("a.py", "x\n")
        eyes = W.Eyes(self.dir)
        eyes.prime()
        os.remove(os.path.join(self.dir, "a.py"))
        self.assertEqual(eyes.look(), ([], ["a.py"]))
        self.assertEqual(eyes.look(), ([], []))

    def test_binary_files_are_tracked_but_not_diffed(self):
        with open(os.path.join(self.dir, "logo.png"), "wb") as f:
            f.write(b"\x89PNG\x00\xff\xfe")
        eyes = W.Eyes(self.dir)
        eyes.prime()
        self.assertIn("logo.png", eyes.hashes)
        self.assertIsNone(eyes.current_text("logo.png"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
