"""Revisit, provenance and speech-policy regressions. Standard library only."""
import contextlib
import hashlib
import io
import json
import os
import platform
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import willow as W
from test_willow import SilentSpeaker


def snapshot(**files):
    return {"files": {p: {"hash": h, "attributes": {}} for p, h in files.items()},
            "uncertain": []}


class TestChangeFacts(unittest.TestCase):
    def test_object_and_attribute_changes_are_distinct(self):
        rows = W.compare_snapshots(snapshot(old="a", edited="b"), snapshot(new="c", edited="d"))
        self.assertEqual([(c["category"], c["action"], c["path"]) for c in rows],
                         [("object", "removed", "old"), ("attribute", "modified", "edited"),
                          ("object", "appeared", "new")])

    def test_a_move_is_labelled_as_inferred_rather_than_proven(self):
        row, = W.compare_snapshots(snapshot(old="a"), snapshot(new="a"))
        self.assertEqual(row["category"], "spatial")
        self.assertIn("inferred move", W.change_sentence(row))

    def test_identical_duplicate_files_do_not_invent_a_move(self):
        rows = W.compare_snapshots(snapshot(a="x", b="x"), snapshot(c="x"))
        self.assertEqual(len(rows), 3)
        self.assertFalse(any(r["category"] == "spatial" for r in rows))

    def test_unreadable_paths_are_not_deletions(self):
        after = snapshot()
        after["uncertain"] = ["src"]
        self.assertEqual(W.compare_snapshots(snapshot(**{"src/a.py": "a"}), after), [])

    def test_repository_wide_read_error_does_not_delete_everything(self):
        after = dict(snapshot(), uncertain=[""])
        self.assertEqual(W.compare_snapshots(snapshot(a="a"), after), [])

    def test_hash_matched_provenance_is_recorded(self):
        row, = W.compare_snapshots(snapshot(a="old"), snapshot(a="new"),
                                  [{"path": "a", "after_hash": "new", "actor": "agent",
                                    "source": "Claude PostToolUse Edit"}])
        self.assertEqual(row["actor"], "agent")
        self.assertIn("agent (recorded)", W.change_sentence(row))

    def test_a_stale_agent_hook_does_not_claim_a_later_user_edit(self):
        row, = W.compare_snapshots(snapshot(a="old"), snapshot(a="new"),
                                  [{"path": "a", "after_hash": "stale", "actor": "agent"}])
        self.assertEqual(row["actor"], "unknown")

    def test_conflicting_authorship_remains_mixed(self):
        evidence = [{"path": "a", "after_hash": "new", "actor": actor}
                    for actor in ("user", "agent")]
        row, = W.compare_snapshots(snapshot(a="old"), snapshot(a="new"), evidence)
        self.assertEqual(row["actor"], "mixed")

    def test_dependency_add_remove_and_version_changes_have_fingerprints(self):
        before = snapshot(**{"package.json": "old"})
        after = snapshot(**{"package.json": "new"})
        before["files"]["package.json"]["attributes"] = W.structured_attributes(
            "package.json", '{"dependencies":{"removed":"1", "changed":"1"}}')
        after["files"]["package.json"]["attributes"] = W.structured_attributes(
            "package.json", '{"dependencies":{"added":"2", "changed":"2"}}')
        rows = W.compare_snapshots(before, after)
        fields = {(r["field"], r["action"], r["category"]) for r in rows if r["field"]}
        self.assertEqual(fields, {("dependencies.removed", "removed", "object"),
                                  ("dependencies.added", "appeared", "object"),
                                  ("dependencies.changed", "modified", "attribute")})

    def test_setting_values_and_secret_keys_are_not_retained(self):
        attrs = W.structured_attributes("settings.json", '{"theme":"SENSITIVE_VALUE", "api_key":"SECRET"}')
        self.assertEqual(list(attrs), ["theme"])
        self.assertNotIn("SENSITIVE_VALUE", json.dumps(attrs))
        self.assertNotIn("SECRET", json.dumps(attrs))

    def test_pyproject_and_requirements_are_supported(self):
        attrs = W.structured_attributes("pyproject.toml", '[project]\nversion="1"\ndependencies=["thing>=2"]\n')
        self.assertIn("project.version", attrs)
        self.assertIn("dependencies.thing", attrs)
        self.assertIn("dependencies.thing", W.structured_attributes("requirements.txt", "thing==2\n"))

    def test_invalid_or_binary_configuration_does_not_invent_attributes(self):
        self.assertEqual(W.structured_attributes("settings.json", "invalid"), {})
        self.assertEqual(W.structured_attributes("settings.json", "[]"), {})

    def test_filenames_cannot_forge_terminal_lines(self):
        self.assertEqual(W.display_path("a\n\x1b[31m.py"), "a\\u000a\\u001b[31m.py")


class MemoryCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="willow-memory-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        for name, value in (("git_context", {}), ("git_ignored_dirs", set())):
            p = patch.object(W, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.cfg = dict(W.DEFAULTS, provider="none", voice=False)

    def write(self, path, text):
        full = os.path.join(self.dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
        return full

    def start(self, **cfg):
        self.speaker = SilentSpeaker()
        self.w = W.Willow(self.dir, self.speaker, dict(self.cfg, **cfg))
        return self.w

    def output(self, fn, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            fn(*args, **kwargs)
        return out.getvalue()

    def settle(self):
        self.w._tick()
        if self.w.ep_touched or self.w.ep_writes:
            self.w.last_change_at = time.time() - 1000
            self.w._tick()


class TestRevisitMemory(MemoryCase):
    def test_a_slow_tick_does_not_split_a_new_write_into_a_premature_episode(self):
        self.start(quiet_seconds=0)
        self.write("a.py", "new")
        self.output(self.w._tick)
        self.assertIn("a.py", self.w.ep_touched)
        self.output(self.w._tick)
        self.assertEqual(self.w.ep_touched, {})

    def test_unknown_authorship_does_not_become_agent_blame_in_stuck_summary(self):
        self.start()
        checks = [{"type": "check", "exit_code": 1, "command": "pytest"}]
        for _ in range(3):
            notes = self.w._update_stuck(checks, {"a.py": 1})
        self.assertEqual(notes, ["a.py changed in 3 recorded attempts that each ended with 'pytest' failing"])

    def test_live_duplicate_content_does_not_invent_a_rename(self):
        self.write("a.py", "same")
        self.write("b.py", "same")
        self.start()
        os.remove(os.path.join(self.dir, "a.py"))
        os.remove(os.path.join(self.dir, "b.py"))
        self.write("c.py", "same")
        out = self.output(self.settle)
        self.assertNotIn("inferred move", out)
        self.assertNotIn("renamed", out)
        self.assertIn("deleted a.py", out)

    def test_break_briefing_includes_changes_made_while_watcher_stays_open(self):
        self.start(speech_mode="auto")
        self.output(self.w.leave_for_break)
        self.assertEqual(self.w.cfg["speech_mode"], "on-demand")
        self.write("during-break.py", "new")
        self.output(self.settle)
        out = self.output(self.w.return_from_break)
        self.assertIn("during-break.py appeared", out)
        self.assertEqual(self.w.cfg["speech_mode"], "auto")
        self.assertIsNone(self.w._away)

    def test_a_second_away_request_does_not_erase_the_break_baseline(self):
        self.start()
        self.output(self.w.leave_for_break)
        before = self.w._away
        self.write("a.py", "new")
        self.output(self.w.leave_for_break)
        self.assertIs(self.w._away, before)

    def test_branch_switches_are_not_presented_as_proof_of_new_work(self):
        with patch.object(W, "git_context", return_value={"branch": "one"}):
            self.start()._save_memory(closed=True)
        with patch.object(W, "git_context", return_value={"branch": "two"}):
            self.start()
        self.assertIn("switching branches", self.output(self.w.briefing))

    def test_first_visit_establishes_a_baseline_without_guessing(self):
        self.start()
        self.assertIn("first recorded session", self.output(self.w.briefing))
        self.assertEqual(self.w.return_changes, [])

    def test_reopen_detects_offline_changes_and_links_evidence(self):
        self.write("a.py", "PRIVATE_SOURCE_BEFORE")
        self.start()._save_memory(closed=True)
        self.write("a.py", "after")
        self.write("new.py", "new")
        self.start()
        self.assertEqual(len(self.w.return_changes), 2)
        out = self.output(self.w.briefing)
        self.assertIn("author unknown", out)
        for c in self.w.return_changes:
            self.assertEqual(self.w.memory.evidence(c["id"])["after_hash"], c["after_hash"])
        with open(self.w.memory.path, "rb") as f:
            self.assertNotIn(b"PRIVATE_SOURCE_BEFORE", f.read())

    def test_repeated_briefings_do_not_consume_changes(self):
        self.start()._save_memory(closed=True)
        self.write("a.py", "a")
        self.start()
        first = self.output(self.w.briefing)
        self.assertEqual(first, self.output(self.w.briefing))

    def test_briefing_is_short_then_expands_on_request(self):
        self.start()._save_memory(closed=True)
        for i in range(8):
            self.write(f"file{i}.py", str(i))
        self.start()
        self.assertIn("3 more changes", self.output(self.w.briefing))
        self.assertNotIn("more changes", self.output(self.w.briefing, full=True))

    def test_unclean_exit_is_explicit(self):
        self.start()
        self.start()
        self.assertIn("without a final checkpoint", self.output(self.w.briefing))

    def test_history_follows_move_candidates_across_sessions(self):
        self.start()._save_memory(closed=True)
        self.write("old.py", "content")
        self.start()._save_memory(closed=True)
        os.rename(os.path.join(self.dir, "old.py"), os.path.join(self.dir, "new.py"))
        self.start()
        rows = self.w.memory.history("new.py")
        self.assertEqual([r["action"] for r in rows], ["relocated", "appeared"])

    def test_corrupt_memory_does_not_silently_reset_the_baseline(self):
        path = self.write(".willow/memory.sqlite3", "broken memory")
        self.start()
        self.assertIsNone(self.w.memory)
        self.assertIn("cannot verify", self.output(self.w.briefing))
        with open(path) as f:
            self.assertEqual(f.read(), "broken memory")

    def test_no_memory_mode_does_not_create_a_ledger(self):
        self.start(memory_enabled=False)
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".willow", "memory.sqlite3")))

    def test_per_file_fact_and_session_retention_are_bounded(self):
        for i in range(5):
            self.write("a.py", str(i))
            self.start(memory_sessions=2, memory_facts=2)._save_memory(closed=True)
        with self.w.memory.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM facts").fetchone()[0], 2)

    def test_partial_checkpoint_rolls_back_facts_and_snapshot(self):
        self.start()
        previous = self.w.memory.latest()
        with self.assertRaises(KeyError):
            self.w.memory.checkpoint(snapshot(a="new"), {}, [{"invalid": True}])
        self.assertEqual(self.w.memory.latest()["snapshot"], previous["snapshot"])

    def test_a_temporary_directory_read_error_preserves_known_files(self):
        self.write("src/a.py", "original")
        self.start()
        real = W.os.scandir
        def scandir(path):
            if os.path.normpath(path) == os.path.join(self.dir, "src"):
                raise PermissionError("temporarily locked")
            return real(path)
        with patch.object(W.os, "scandir", side_effect=scandir):
            self.w.eyes.look()
            changes = self.w._save_memory()
        self.assertEqual(changes, [])
        self.assertIn("src/a.py", self.w._memory_snapshot["files"])

    def test_private_files_do_not_enter_memory(self):
        self.write(".env", "PASSWORD=secret")
        self.write("credentials.json", '{"password":"secret"}')
        self.start()
        self.assertEqual(self.w._memory_snapshot["files"], {})

    def test_old_test_results_are_not_replayed_as_new(self):
        self.start()._save_memory(closed=True)
        self.write(".willow/inbox.jsonl", json.dumps({"type": "check", "exit_code": 1, "command": "old test"}) + "\n")
        self.start()
        self.assertEqual(self.w._read_new_events(), [])

    def test_new_unverifiable_checks_never_turn_into_failure_verdicts(self):
        self.start()
        self.write(".willow/inbox.jsonl", '{"type":"check","command":"test"}\n')
        events = self.w._read_new_events()
        self.assertEqual(events[0]["type"], "notice")

    def test_offline_recorded_provenance_requires_matching_content(self):
        self.start()._save_memory(closed=True)
        path = self.write("a.py", "agent content")
        self.write(".willow/inbox.jsonl", json.dumps({
            "type": "write", "ts": time.time() + 0.1, "path": path,
            "actor": "agent", "after_hash": hashlib.sha1(b"agent content").hexdigest(),
            "source": "Claude PostToolUse Write"}) + "\n")
        self.start()
        self.assertEqual(self.w.return_changes[0]["actor"], "agent")

    def test_live_change_labels_and_history_use_the_same_provenance(self):
        self.start()
        path = self.write("a.py", "a")
        self.write(".willow/inbox.jsonl", json.dumps({
            "type": "write", "ts": time.time(), "path": path,
            "actor": "user", "after_hash": hashlib.sha1(b"a").hexdigest(),
            "source": "explicit declaration"}) + "\n")
        out = self.output(self.settle)
        self.assertIn("[user recorded]", out)
        self.assertEqual(self.w.memory.history("a.py")[0]["actor"], "user")

    def test_cli_briefing_does_not_advance_the_baseline(self):
        self.start()._save_memory(closed=True)
        before = self.w.memory.latest()
        self.write("a.py", "a")
        self.output(W.cmd_memory, "briefing", ["--folder", self.dir])
        self.assertEqual(self.w.memory.latest(), before)

    def test_quit_saves_edits_that_arrive_before_the_next_poll(self):
        def commands():
            self.write("last-edit.py", "content")
            yield "q\n"
        with patch.object(W.sys, "stdin", commands()), \
                patch.object(W, "Speaker", side_effect=lambda **kwargs: SilentSpeaker()), \
                patch.object(W, "resolve_provider", return_value=W.Provider()):
            out = self.output(W.cmd_watch, [self.dir, "--no-voice", "--provider", "none"])
        memory = W.ProjectMemory(self.dir, self.cfg)
        self.assertTrue(memory.latest()["closed"])
        self.assertIn("last-edit.py", memory.latest()["snapshot"]["files"])
        self.assertIn("stopped watching", out)

    def test_credential_file_cannot_be_declared_for_provenance(self):
        self.write(".env", "SECRET=abc")
        out = self.output(W.cmd_memory, "record", ["--folder", self.dir, "--actor", "user", ".env"])
        self.assertIn("couldn't record", out)

    def test_newer_memory_schema_is_preserved_and_reported(self):
        self.start()
        with self.w.memory.db() as db:
            db.execute("PRAGMA user_version=999")
        self.start()
        self.assertIsNone(self.w.memory)
        self.assertIn("newer Willow", self.w.memory_error)

    def test_invalid_saved_snapshot_does_not_crash_startup(self):
        self.start()
        with self.w.memory.db() as db:
            db.execute("UPDATE sessions SET snapshot='[]'")
        self.start()
        self.assertIsNone(self.w.memory)
        self.assertIn("invalid saved", self.w.memory_error)


class TestSpeechPolicy(MemoryCase):
    def test_large_announcements_speak_a_bounded_summary_and_print_all_details(self):
        self.start(speech_mode="auto")
        details = ["detail %d" % i for i in range(10)]
        out = self.output(self.w.announce_lines, "; ".join(details), details)
        self.assertIn("detail 9", out)
        spoken = self.speaker.spoken[0][1]
        self.assertNotIn("detail 9", spoken)
        self.assertIn("5 more details", spoken)

    def test_failures_are_spoken_before_routine_file_changes(self):
        self.start(speech_mode="auto")
        self.write("a.py", "new")
        self.write(".willow/inbox.jsonl", '{"type":"check","exit_code":1,"command":"pytest"}\n')
        self.output(self.settle)
        self.assertTrue(self.speaker.spoken[0][1].startswith("then 'pytest' FAILED"))

    def test_windows_lock_screen_suppresses_project_speech(self):
        speaker = W.Speaker(enabled=False)
        self.addCleanup(speaker.close)
        speaker.system = "Windows"
        with patch.object(W.ScreenReaderBridge, "desktop_available", return_value=False), \
                patch.object(speaker, "_ensure_host") as host:
            speaker._speak_blocking("private project")
        host.assert_not_called()

    def test_nvda_bridge_uses_a_checked_client_and_unicode_text(self):
        import ctypes
        client = Mock()
        client.nvdaController_testIfRunning.return_value = 0
        client.nvdaController_speakText.return_value = 0
        with patch.object(W.platform, "system", return_value="Windows"), \
                patch.object(ctypes, "WinDLL", return_value=client, create=True):
            bridge = W.ScreenReaderBridge("nvda", "client.dll")
        self.assertEqual(bridge.backend, "nvda")
        bridge.speak("\u30c6\u30b9\u30c8.py")
        client.nvdaController_speakText.assert_called_once_with("\u30c6\u30b9\u30c8.py")

    def test_unavailable_jaws_has_an_explicit_system_fallback(self):
        with patch.object(W.platform, "system", return_value="Windows"), \
                patch.object(W.subprocess, "run", return_value=Mock(returncode=1)):
            bridge = W.ScreenReaderBridge("jaws")
        self.assertEqual(bridge.backend, "system")
        self.assertIn("JAWS COM speech is unavailable", bridge.problem)

    def test_automatic_updates_are_text_only_by_default(self):
        self.start()
        self.write("a.py", "a")
        out = self.output(self.settle)
        self.assertIn("created a.py", out)
        self.assertEqual(self.speaker.spoken, [])

    def test_on_demand_answers_are_spoken_and_can_be_repeated(self):
        self.start()
        self.output(self.w.state_summary)
        self.output(self.w.state_summary)
        self.assertEqual(len(self.speaker.spoken), 2)

    def test_auto_mode_speaks_at_an_episode_boundary(self):
        self.start(speech_mode="auto")
        self.write("a.py", "a")
        self.output(self.settle)
        self.assertEqual(len(self.speaker.spoken), 1)

    def test_routine_repetition_is_filtered_but_requested_repetition_is_not(self):
        self.start(speech_mode="auto")
        for _ in range(2):
            self.output(self.w._automatic, self.w.announce, "same fact")
        self.assertEqual(len(self.speaker.spoken), 1)
        self.output(self.w.announce, "same fact")
        self.assertEqual(len(self.speaker.spoken), 2)

    def test_quiet_mode_keeps_failures_and_filters_routine_speech(self):
        self.start(speech_mode="auto", verbosity="quiet")
        self.output(self.w._automatic, self.w.announce, "routine")
        self.output(self.w._automatic, self.w.announce, "failure", urgent=True)
        self.assertEqual([t for _, t in self.speaker.spoken], ["failure"])

    def test_mac_speech_uses_native_say(self):
        speaker = W.Speaker(enabled=False)
        self.addCleanup(speaker.close)
        speaker.system = "Darwin"
        with patch.object(W.subprocess, "Popen") as popen:
            speaker._speak_blocking("a fact")
        self.assertEqual(popen.call_args[0][0][0], "say")

    def test_linux_tries_espeak_when_speech_dispatcher_is_not_available(self):
        speaker = W.Speaker(enabled=False)
        self.addCleanup(speaker.close)
        speaker.system = "Linux"
        proc = Mock(returncode=0)
        with patch.object(W.subprocess, "Popen", side_effect=[FileNotFoundError(), proc]) as popen:
            speaker._speak_blocking("a fact")
        self.assertEqual(popen.call_args[0][0][0], "espeak")

    def test_nonzero_speech_dispatcher_exit_tries_espeak(self):
        speaker = W.Speaker(enabled=False)
        self.addCleanup(speaker.close)
        speaker.system = "Linux"
        with patch.object(W.subprocess, "Popen", side_effect=[Mock(returncode=1), Mock(returncode=0)]) as popen:
            speaker._speak_blocking("a fact")
        self.assertEqual(popen.call_count, 2)

    def test_windows_only_backend_falls_back_explicitly_on_other_platforms(self):
        with patch.object(W.platform, "system", return_value="Linux"):
            bridge = W.ScreenReaderBridge("nvda")
        self.assertEqual(bridge.backend, "system")
        self.assertIn("only available on Windows", bridge.problem)

    def test_invalid_settings_do_not_crash_or_spin_the_watcher(self):
        self.write(".willow/config.json", '{"poll_seconds":0, "briefing_limit":"oops", "speech_mode":"oops"}')
        with contextlib.redirect_stderr(io.StringIO()):
            cfg = W.load_config(self.dir)
        self.assertEqual(cfg["poll_seconds"], W.DEFAULTS["poll_seconds"])
        self.assertEqual(cfg["briefing_limit"], W.DEFAULTS["briefing_limit"])
        self.assertEqual(cfg["speech_mode"], "on-demand")


if __name__ == "__main__":
    unittest.main()
