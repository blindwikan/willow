"""Focus, browsing, version freshness, handover and opt-in study regressions."""
import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import willow as W
from test_memory import MemoryCase
from test_willow import SilentSpeaker


class TestFocus(MemoryCase):
    def test_a_real_watcher_with_open_keyboard_input_does_not_stall_git_or_lose_a_result(self):
        # Simulations miss Windows background Git inheriting an open keyboard
        # pipe. Exercise the standalone distribution in two real processes.
        self.write("app.py", "before")
        self.write(".willow/config.json", json.dumps(dict(self.cfg, quiet_seconds=0)))
        transcript = os.path.join(self.dir, ".willow", "watch-output.txt")
        events = os.path.join(self.dir, ".willow", W.EVENTS_NAME)
        program = [sys.executable, os.path.abspath(W.__file__)]
        env = dict(os.environ)
        env.pop("WILLOW_DIR", None)

        def read(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except FileNotFoundError:
                return ""

        def wait_for(predicate):
            deadline = W.time.monotonic() + 12
            while W.time.monotonic() < deadline:
                if predicate():
                    return
                W.time.sleep(0.05)
            self.fail("Watcher stalled: " + read(transcript))

        with open(transcript, "w", encoding="utf-8") as output:
            proc = subprocess.Popen(program + ["watch", self.dir, "--focus", "--no-voice", "--provider", "none", "--poll", "0.05"],
                                    stdin=subprocess.PIPE, stdout=output, stderr=output, env=env,
                                    text=True, encoding="utf-8", cwd=self.dir)
            try:
                wait_for(lambda: "Focus mode is on" in read(transcript))
                self.write("app.py", "after")
                subprocess.check_output(program + ["run", sys.executable, "-c", "pass"],
                                        cwd=self.dir, env=env, stderr=subprocess.STDOUT, timeout=15)
                wait_for(lambda: '"exit_code": 0' in read(events))
                self.assertNotIn("app.py", read(transcript))
                proc.communicate("review\nnext\nnext\nq\n", timeout=15)
                self.assertEqual(proc.returncode, 0, read(transcript))
            finally:
                if proc.poll() is None:
                    proc.communicate("q\n", timeout=12)
        out = read(transcript)
        self.assertIn("app.py", out)
        self.assertIn("passed", out)
        self.assertIn("test coverage not established", out)

    def test_focus_does_not_print_or_speak_even_an_automatic_failure(self):
        self.start(focus=True, speech_mode="auto")
        out = self.output(self.w._automatic, self.w.announce, "Build failed.", urgent=True)
        self.assertEqual(out, "")
        self.assertEqual(self.speaker.spoken, [])
        self.assertIn("Build failed.", self.output(self.w.review_pending))
        self.assertTrue(self.w.focus)

    def test_focus_keeps_facts_while_suppressing_the_live_terminal(self):
        self.start(focus=True)
        self.write("new.py", "source")
        self.assertEqual(self.output(self.w._automatic, self.settle), "")
        self.assertIn("new.py", self.output(self.w.browse_changes))
        with open(self.w.log_path, encoding="utf-8") as f:
            self.assertIn("new.py", f.read())

    def test_explicit_requests_still_answer_in_focus_mode(self):
        self.start(focus=True, speech_mode="auto")
        self.assertIn("hello", self.output(self.w.announce, "hello"))
        self.assertEqual(self.speaker.spoken[-1][1], "hello")

    def test_turning_focus_off_does_not_dump_the_buffer(self):
        self.start(focus=True)
        self.output(self.w._automatic, self.w.announce, "Buffered secret marker.")
        out = self.output(self.w.set_focus, False)
        self.assertNotIn("secret marker", out)
        self.assertIn("secret marker", self.output(self.w.review_pending))

    def test_buffer_overflow_is_explicit_and_review_clear_keeps_history(self):
        self.start(focus=True, focus_buffer_limit=2)
        for text in ("oldest", "middle", "newest"):
            self.output(self.w._automatic, self.w.announce, text)
        out = self.output(self.w.review_pending)
        self.assertIn("1 older updates", out)
        self.assertIn("item 1 of 2: middle", out)
        self.output(self.w.review_pending, clear=True)
        self.assertIn("No buffered updates", self.output(self.w.review_pending))
        with open(self.w.log_path, encoding="utf-8") as f:
            self.assertIn("oldest", f.read())

    def test_nested_automatic_context_cannot_leak_text_during_focus(self):
        self.start(focus=True)

        def nested():
            self.w._automatic(self.w.announce, "first")
            self.w.announce("second")

        self.assertEqual(self.output(self.w._automatic, nested), "")
        self.assertIn("explicit", self.output(self.w.announce, "explicit"))

    def test_focus_startup_buffers_briefing_and_private_notes(self):
        self.start()._save_memory(closed=True)
        self.write("new.py", "source")
        self.w.notes.save("working", "PRIVATE_NOTE")
        self.start(focus=True)
        self.w.stop.set()
        out = self.output(self.w.run)
        self.assertIn("Focus mode is on", out)
        self.assertNotIn("new.py appeared", out)
        self.assertNotIn("PRIVATE_NOTE", out)
        self.output(self.w.review_pending)
        self.output(self.w.navigate, "next")
        out = self.output(self.w.navigate, "next")
        self.assertIn("PRIVATE_NOTE", out)
        with open(self.w.log_path, encoding="utf-8") as f:
            self.assertNotIn("PRIVATE_NOTE", f.read())


class TestChangeBrowser(MemoryCase):
    def test_navigation_has_positions_repeat_and_no_silent_wraparound(self):
        self.start(focus=True)
        for text in ("first", "second"):
            self.output(self.w._automatic, self.w.announce, text)
        self.assertIn("item 1 of 2: first", self.output(self.w.review_pending))
        self.assertEqual(self.output(self.w.navigate, "previous"), "Willow: Already at the first item.\n")
        out = self.output(self.w.navigate, "next")
        self.assertIn("item 2 of 2: second", out)
        self.assertEqual(self.output(self.w.navigate, "repeat"), out)
        self.assertEqual(self.output(self.w.navigate, "next"), "Willow: Already at the last item.\n")
        self.assertIn("first", self.output(self.w.navigate, "previous"))

    def test_buffered_details_expand_only_on_request(self):
        self.start(focus=True)
        self.output(self.w._automatic, self.w.announce_lines, "Two changes.", ["detail one", "detail two"])
        self.assertNotIn("detail one", self.output(self.w.review_pending))
        out = self.output(self.w.navigate, "expand")
        self.assertIn("detail one", out)
        self.assertIn("detail two", out)

    def test_review_is_a_stable_snapshot_while_new_changes_arrive(self):
        self.start(focus=True)
        self.output(self.w._automatic, self.w.announce, "first")
        self.output(self.w.review_pending)
        self.output(self.w._automatic, self.w.announce, "later")
        self.assertIn("item 1 of 1", self.output(self.w.navigate, "repeat"))
        self.assertIn("item 1 of 2", self.output(self.w.review_pending))

    def test_history_expands_verified_evidence_for_its_current_item(self):
        self.write("space name.py", "one")
        self.start()
        for text in ("two", "three"):
            self.write("space name.py", text)
            self.output(self.settle)
        self.assertIn("history for space name.py, item 1 of 2", self.output(self.w.file_history, "space name.py"))
        self.assertIn("Evidence", self.output(self.w.navigate, "expand"))
        self.assertIn("item 2 of 2", self.output(self.w.navigate, "next"))

    def test_empty_history_replaces_an_old_list(self):
        self.start(focus=True)
        self.output(self.w._automatic, self.w.announce, "old")
        self.output(self.w.review_pending)
        self.output(self.w.file_history, "missing.py")
        self.assertIn("No active change list", self.output(self.w.navigate, "repeat"))

    def test_return_briefing_supplies_a_navigable_fact_list(self):
        self.start()._save_memory(closed=True)
        self.write("one.py", "one")
        self.write("two.py", "two")
        self.start()
        self.output(self.w.briefing)
        self.assertIn("return changes, item 2 of 2", self.output(self.w.navigate, "next"))

    def test_empty_briefing_cannot_leave_old_unrelated_items_active(self):
        self.start(focus=True)
        self.output(self.w._automatic, self.w.announce, "old")
        self.output(self.w.review_pending)
        self.output(self.w.briefing)
        self.assertIn("No active change list", self.output(self.w.navigate, "repeat"))


class TestFreshness(MemoryCase):
    def check(self):
        state = W.observed_check_state(self.dir)
        return {"type": "check", "command": "tests", "exit_code": 0,
                "verification": {"before": state, "after": state}}

    def test_matching_versions_do_not_claim_test_coverage(self):
        self.write("app.py", "one")
        self.start()
        out = self.output(self.w._close_episode, [self.check()])
        self.assertIn("matches observed project state at both command endpoints; test coverage not established", out)

    def test_a_later_edit_cannot_reuse_a_fresh_pass(self):
        self.write("app.py", "one")
        self.start()
        self.output(self.w._close_episode, [self.check()])
        self.write("app.py", "two")
        out = self.output(self.w.check_summary)
        self.assertIn("passed; observed project state differs from this check", out)

    def test_commands_that_change_files_have_an_uncertain_tested_version(self):
        self.write("app.py", "one")
        check = self.check()
        self.write("app.py", "two")
        current = W.observed_check_state(self.dir)
        check["verification"]["after"] = current
        self.assertEqual(W.check_freshness(check, current),
                         "observed files changed during the command; tested version uncertain")

    def test_missing_hook_versions_and_unreadable_files_remain_unknown(self):
        check = self.check()
        current = W.observed_check_state(self.dir)
        self.assertEqual(W.check_freshness({"exit_code": 0}, current), "tested version not recorded")
        current["uncertain"] = True
        self.assertEqual(W.check_freshness(check, current), "tested version could not be verified")

    def test_another_folder_or_malformed_signature_cannot_match(self):
        check = self.check()
        current = dict(W.observed_check_state(self.dir), folder="different-project")
        self.assertEqual(W.check_freshness(check, current), "tested version could not be verified")
        for state in check["verification"].values():
            state["signature"] = "made up"
        self.assertEqual(W.check_freshness(check, W.observed_check_state(self.dir)),
                         "tested version could not be verified")

    def test_ignored_runtime_files_do_not_invalidate_a_result(self):
        check = self.check()
        self.write(".willow/runtime.json", "changing logs")
        self.assertIn("matches observed", W.check_freshness(check, W.observed_check_state(self.dir)))

    def test_git_revision_change_alone_invalidates_freshness(self):
        with patch.object(W, "git_context", return_value={"head": "before"}):
            check = self.check()
        with patch.object(W, "git_context", return_value={"head": "after"}):
            self.assertIn("differs", W.check_freshness(check, W.observed_check_state(self.dir)))

    def test_results_survive_reopening_and_keep_their_original_observation(self):
        self.write("app.py", "one")
        self.start()
        self.output(self.w._close_episode, [self.check()])
        self.w._save_memory(closed=True)
        self.write("app.py", "two")
        self.start()
        self.assertIn("differs", self.output(self.w.check_summary))
        self.assertEqual(len(self.w._checks), 1)

    def test_history_is_bounded_to_twenty_actual_results(self):
        self.start()
        for i in range(25):
            self.w.memory.save_check({"exit_code": i % 2, "command": "tests " + str(i)})
        checks = self.w.memory.recent_checks()
        self.assertEqual(len(checks), 20)
        self.assertEqual(checks[0]["command"], "tests 24")
        self.assertEqual(checks[-1]["command"], "tests 5")

    def test_retained_results_do_not_copy_failure_source_snippets(self):
        self.start()
        self.w.memory.save_check({"exit_code": 1, "command": "tests", "detail": "PRIVATE_SOURCE", "output": "PRIVATE_OUTPUT"})
        encoded = json.dumps(self.w.memory.recent_checks())
        self.assertNotIn("PRIVATE", encoded)
        self.assertEqual(W.check_freshness(self.w.memory.recent_checks()[0], W.observed_check_state(self.dir)),
                         "tested version not recorded")

    def test_windows_unsigned_exit_status_stays_consistent_in_retained_results(self):
        self.start()
        self.output(self.w._close_episode, [{"type": "check", "exit_code": 4294963238, "command": "tests"}])
        self.assertIn("failed with exit code -4058", self.output(self.w.check_summary))

    def test_real_wrapped_command_records_endpoint_hashes_and_true_exit(self):
        self.write("app.py", "one")
        recorded = []
        target = os.path.join(self.dir, ".willow")
        with patch.object(W, "resolve_target_dir", return_value=(target, True)), patch.object(W, "deliver", side_effect=recorded.append):
            self.assertEqual(self.output(W.cmd_run, [sys.executable, "-c", "pass"]).count("passed"), 1)
        record, = recorded
        self.assertEqual(record["exit_code"], 0)
        self.assertIn("matches observed", W.check_freshness(record, W.observed_check_state(self.dir)))
        self.assertNotIn("app.py", json.dumps(record["verification"]))

    def test_real_wrapped_mutation_does_not_claim_an_unchanged_version(self):
        path = self.write("app.py", "one")
        recorded = []
        script = "from pathlib import Path; Path(" + repr(path) + ").write_text('two')"
        with patch.object(W, "resolve_target_dir", return_value=(os.path.join(self.dir, ".willow"), True)), patch.object(W, "deliver", side_effect=recorded.append):
            self.output(W.cmd_run, [sys.executable, "-c", script])
        self.assertIn("changed during", W.check_freshness(recorded[0], W.observed_check_state(self.dir)))


class TestHandover(MemoryCase):
    def test_notes_preserve_case_spaces_and_stay_separate_from_facts_and_logs(self):
        self.start()
        self.output(self.w.support_command, "note next Check the OpenAI Button")
        self.assertIn("User note (not verified). Next step: Check the OpenAI Button", self.output(self.w.handover))
        self.assertEqual(self.w.memory.recent(), [])
        with open(self.w.log_path, encoding="utf-8") as f:
            self.assertNotIn("Check the OpenAI Button", f.read())

    def test_note_controls_cannot_forge_terminal_lines(self):
        self.start()
        self.w.notes.save("working", "line\nforged\x1b")
        out = self.output(self.w.handover)
        self.assertIn("line\\u000aforged\\u001b", out)

    def test_notes_return_after_restart_and_clear_only_the_requested_field(self):
        self.start()
        self.w.notes.save("working", "work")
        self.w.notes.save("next", "next")
        self.w._save_memory(closed=True)
        self.start()
        self.assertIn("What I was doing: work", self.output(self.w.handover))
        self.output(self.w.handover, "working", clear=True)
        self.assertEqual(self.w.notes.read(), {"next": "next"})
        self.output(self.w.handover, clear=True)
        self.assertTrue(os.path.exists(self.w.memory.path))

    def test_bad_existing_notes_are_preserved_instead_of_overwritten(self):
        self.start()
        path = self.write(".willow/handover.json", "{corrupt")
        self.assertIn("Could not read or save", self.output(self.w.handover, "next", "replacement"))
        with open(path) as f:
            self.assertEqual(f.read(), "{corrupt")

    def test_invalid_or_oversized_notes_do_not_replace_existing_notes(self):
        notes = W.HandoverNotes(self.dir)
        notes.save("next", "original")
        for key, value in (("guess", "bad"), ("next", "x" * 4001)):
            with self.assertRaises(ValueError):
                notes.save(key, value)
        self.assertEqual(notes.read(), {"next": "original"})

    def test_handover_cli_is_available_without_a_watcher(self):
        self.assertIn("Next step: Review tests", self.output(W.main, ["handover", "--folder", self.dir, "--next", "Review tests"]))
        self.assertIn("no user handover notes", self.output(W.main, ["handover", "--folder", self.dir, "--clear"]))


class TestStudyMetrics(MemoryCase):
    def test_ordinary_use_and_status_do_not_create_a_study_database(self):
        self.start()
        self.output(self.w.announce, "hello")
        self.output(self.w.support_command, "study status")
        self.w.record_request("state")
        self.assertFalse(os.path.exists(self.w.study.path))

    def test_start_is_explicit_and_cannot_restart_an_active_recording(self):
        recorder = W.StudyRecorder(self.dir)
        for code in ("", "Name with spaces", "a@example.com"):
            with self.assertRaises(ValueError):
                recorder.start(code)
        self.assertFalse(os.path.exists(recorder.path))
        recorder.start("P01")
        with self.assertRaisesRegex(ValueError, "already active"):
            recorder.start("P02")
        self.assertEqual(recorder.active()["participant"], "P01")

    def test_stopped_recording_rejects_new_events(self):
        recorder = W.StudyRecorder(self.dir)
        recorder.start("P01")
        recorder.record("command", name="stop")
        recorder.stop()
        self.assertFalse(recorder.record("command", name="repeat"))
        report = recorder.export()
        self.assertFalse(report["recording_active"])
        self.assertEqual(report["metrics"]["stop_requests"], 1)
        self.assertEqual(report["metrics"]["repeat_requests"], 0)

    def test_phase_durations_use_relative_time_and_collisions_are_reported(self):
        recorder = W.StudyRecorder(self.dir)
        with patch.object(W.time, "time", return_value=100):
            recorder.start("P01")
            recorder.record("phase", label="setup")
        with patch.object(W.time, "time", return_value=110):
            recorder.record("phase", label="task-A")
            recorder.record("collision_reported")
        with patch.object(W.time, "time", return_value=130):
            recorder.stop()
        report = recorder.export()
        self.assertEqual(report["duration_seconds"], 30)
        self.assertEqual(report["metrics"]["phase_seconds"], {"setup": 10, "task-A": 20})
        self.assertEqual(report["metrics"]["reported_collisions"], 1)
        self.assertNotIn("started", report)
        self.assertIn("audible delivery and task accuracy are not inferred", report["measurement_limits"])

    def test_notes_paths_commands_and_unknown_input_never_enter_study_export(self):
        self.start(focus=True, speech_mode="auto", voice=True)
        self.w.study.start("P01")
        for raw in ("note working PRIVATE_NOTE", "history PRIVATE_PATH.py", "repeat", "shh", "PRIVATE_COMMAND --key SECRET"):
            self.w.record_request(raw)
        self.output(self.w.support_command, "note working PRIVATE_NOTE")
        self.output(self.w._automatic, self.w.announce, "PRIVATE_ANNOUNCEMENT")
        self.output(self.w.review_pending)
        self.w.study.record("command", name="PRIVATE_COMMAND")
        self.w.study.record("phase", label="SECRET_PHASE")
        self.w.study.record("episode", file_count=1, check_count=0, failure_count=0, path="PRIVATE_PATH")
        report = self.w.study.export()
        encoded = json.dumps(report)
        for secret in ("PRIVATE", "SECRET", self.dir, ".py", "signature", "before_hash"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(report["metrics"]["repeat_requests"], 1)
        self.assertEqual(report["metrics"]["stop_requests"], 1)
        self.assertEqual(report["metrics"]["event_counts"]["focus_buffered"], 1)

    def test_tampered_or_invalid_events_are_dropped_at_export(self):
        recorder = W.StudyRecorder(self.dir)
        session = recorder.start("P01")
        with recorder.db() as db:
            for kind, data in (("command", '{"name":"SECRET_COMMAND"}'), ("episode", "[]"),
                               ("episode", "corrupt"), ("focus_buffered", '{"detail_count":-4}'),
                               ("phase", '{"label":"SECRET_PHASE"}')):
                db.execute("INSERT INTO study_events (session, elapsed, kind, data) VALUES (?, 0, ?, ?)", (session, kind, data))
        self.assertEqual(recorder.export()["events"], [])

    def test_invalid_session_metadata_is_not_exported(self):
        recorder = W.StudyRecorder(self.dir)
        recorder.start("P01")
        with recorder.db() as db:
            db.execute("UPDATE study_sessions SET participant='SECRET EMAIL'")
        with self.assertRaisesRegex(ValueError, "invalid study session metadata"):
            recorder.export()

    def test_selected_session_export_does_not_mix_participants(self):
        recorder = W.StudyRecorder(self.dir)
        first = recorder.start("P01")
        recorder.record("command", name="repeat")
        recorder.stop()
        recorder.start("P02")
        self.assertEqual(recorder.export()["participant_code"], "P02")
        self.assertEqual(recorder.export(first)["metrics"]["repeat_requests"], 1)
        self.assertEqual(recorder.export()["metrics"]["repeat_requests"], 0)

    def test_recording_survives_watcher_restart_and_is_visible_at_startup(self):
        self.start()
        self.w.study.start("P01")
        self.start()
        self.w.stop.set()
        self.assertIn("Opt-in study recording is active", self.output(self.w.run))

    def test_metrics_storage_failure_does_not_crash_or_repeat_forever(self):
        self.start()
        with patch.object(self.w.study, "record", side_effect=sqlite3.OperationalError("locked")):
            self.assertIn("Study metrics could not be saved", self.output(self.w.record_request, "state"))
            self.assertEqual(self.output(self.w.record_request, "state"), "")

    def test_study_cli_starts_marks_stops_and_exports_without_a_watcher(self):
        for args in (["start", "P01"], ["mark", "setup"], ["collision"], ["stop"], ["export"]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(W.main(["study"] + args + ["--folder", self.dir]), 0)
        with open(os.path.join(self.dir, ".willow", "study-export.json")) as f:
            self.assertEqual(json.load(f)["metrics"]["reported_collisions"], 1)

    def test_watch_routes_next_to_the_browser_and_records_canonical_actions(self):
        self.start()
        self.w.study.start("P01")
        self.w._pending_output = [{"text": "first", "details": []}, {"text": "second", "details": []}]
        with patch.object(W, "Willow", return_value=self.w), patch.object(W, "Speaker", return_value=SilentSpeaker()), patch.object(W, "Viewer"), patch.object(W, "resolve_provider"), patch.object(W.threading, "Thread") as thread, patch.object(W, "watcher_status", return_value=(False, {})), patch.object(W.sys, "stdin", io.StringIO("review\nnext\nrepeat\nshh\nq\n")):
            thread.return_value.is_alive.return_value = True
            out = self.output(W.cmd_watch, [self.dir, "--focus", "--no-voice"])
        self.assertIn("item 2 of 2: second", out)
        self.assertEqual(self.w.study.export()["metrics"]["repeat_requests"], 1)
        self.assertEqual(self.w.study.export()["metrics"]["stop_requests"], 1)


if __name__ == "__main__":
    unittest.main()
