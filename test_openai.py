"""OpenAI wire-contract and Codex hook regressions; no accounts or SDK needed."""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import willow as W


class TestOpenAIResponses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        outer = cls
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, dict(self.headers), body))
                encoded = json.dumps(outer.reply).encode("utf-8")
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = "http://127.0.0.1:%d/v1" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=3)

    def setUp(self):
        type(self).requests = []
        type(self).status = 200
        type(self).reply = {"status": "completed", "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "edited app.py."}]}]}
        self.provider = W.OpenAIProvider(api_key="test-key", base_url=self.url)

    def test_text_uses_responses_and_does_not_request_storage(self):
        self.assertEqual(self.provider.text("sys", "facts", 200, 5), "edited app.py.")
        path, headers, body = self.requests[0]
        self.assertEqual(path, "/v1/responses")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(body["instructions"], "sys")
        self.assertEqual(body["input"], "facts")
        self.assertEqual(body["max_output_tokens"], 200)
        self.assertFalse(body["store"])

    def test_vision_uses_the_image_model_and_responses_image_shape(self):
        self.provider.vision_model = "chosen-vision-model"
        self.provider.vision("sys", "look", b"PNG", 100, 5)
        body = self.requests[0][2]
        self.assertEqual(body["model"], "chosen-vision-model")
        parts = body["input"][0]["content"]
        self.assertEqual(parts[0], {"type": "input_text", "text": "look"})
        self.assertEqual(parts[1]["type"], "input_image")
        self.assertTrue(parts[1]["image_url"].startswith("data:image/png;base64,"))

    def test_reasoning_items_are_not_spoken_as_output(self):
        self.assertEqual(self.provider.text("s", "f", 200, 5), "edited app.py.")

    def test_incomplete_or_failed_responses_are_not_narrated(self):
        for status in ("incomplete", "failed", "in_progress", "cancelled"):
            with self.subTest(status=status):
                type(self).reply["status"] = status
                self.assertIsNone(self.provider.text("s", "f", 200, 5))

    def test_refusals_are_not_spoken_as_a_factual_description(self):
        type(self).reply["output"][1]["content"] = [{"type": "refusal", "refusal": "no"}]
        self.assertIsNone(self.provider.text("s", "f", 200, 5))

    def test_bad_credentials_and_rate_limits_fall_back_without_a_retry_storm(self):
        for status in (401, 429, 500):
            with self.subTest(status=status):
                type(self).status = status
                type(self).requests = []
                self.assertIsNone(self.provider.text("s", "f", 200, 5))
                self.assertEqual(len(self.requests), 1)

    def test_malformed_output_is_survivable(self):
        type(self).reply = {"status": "completed", "output": [{"type": "message", "content": [None]}]}
        self.assertIsNone(self.provider.text("s", "f", 200, 5))

    def test_explicit_openai_selects_responses(self):
        provider = W.resolve_provider(dict(W.DEFAULTS, provider="openai"))
        self.assertIsInstance(provider, W.OpenAIProvider)

    def test_legacy_openai_mode_remains_explicitly_available(self):
        provider = W.resolve_provider(dict(W.DEFAULTS, provider="openai", openai_api="chat-completions"))
        self.assertIs(type(provider), W.OpenAICompatProvider)

    def test_compatible_servers_keep_chat_completions(self):
        provider = W.resolve_provider(dict(W.DEFAULTS, provider="openai-compat"))
        self.assertIs(type(provider), W.OpenAICompatProvider)

    def test_vision_token_parameter_uses_vision_model_not_text_model(self):
        provider = W.OpenAICompatProvider(api_key="test", text_model="gpt-6-sol", vision_model="gpt-4o-mini")
        calls = []
        def post(url, headers, payload, timeout):
            calls.append(payload)
            return {"choices": [{"message": {"content": "a form"}}]}
        with patch.object(W, "_post_json", side_effect=post):
            provider.vision("s", "p", b"png", 100, 5)
        self.assertIn("max_tokens", calls[0])
        self.assertNotIn("max_completion_tokens", calls[0])

    def test_chat_retry_does_not_repeat_the_same_invalid_parameter(self):
        calls = []
        def post(url, headers, payload, timeout):
            calls.append(payload)
            # A real HTTP error owns a response stream. Python 3.8/3.9 cannot
            # close the incomplete fp=None fixture used by the original test.
            raise urllib.error.HTTPError(url, 400, "bad parameter", {}, io.BytesIO(b"{}"))
        with patch.object(W, "_post_json", side_effect=post):
            self.assertIsNone(W.OpenAICompatProvider(api_key="test").text("s", "f", 100, 5))
        self.assertEqual(len(calls), 2)


class TestCodexHooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="willow-codex-")
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name
        os.makedirs(os.path.join(self.folder, ".willow"))
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("WILLOW_DIR", None)

    def write(self, name, text):
        path = os.path.join(self.folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def fire(self, event, **fields):
        payload = dict(cwd=self.folder, tool_name="apply_patch", tool_use_id="call-1",
                       tool_input={"command": "*** Begin Patch\n*** Update File: a.py\n*** End Patch"})
        payload.update(fields)
        with patch.object(W.sys, "stdin", io.StringIO(json.dumps(payload))), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(W.cmd_hook([event, "--agent", "codex"]), 0)
        self.assertEqual(json.loads(out.getvalue()), {})
        path = os.path.join(self.folder, ".willow", "inbox.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_matched_patch_hooks_record_changed_content(self):
        self.write("a.py", "before")
        self.fire("PreToolUse")
        self.write("a.py", "after")
        rows = self.fire("PostToolUse")
        self.assertEqual(rows[0]["actor"], "agent")
        self.assertIn("Codex", rows[0]["source"])
        self.assertTrue(rows[0]["after_hash"])

    def test_failed_or_unchanged_patch_does_not_invent_a_write(self):
        self.write("a.py", "before")
        self.fire("PreToolUse")
        self.assertEqual(self.fire("PostToolUse"), [])

    def test_missing_pre_hook_keeps_authorship_unknown(self):
        self.write("a.py", "after")
        self.assertEqual(self.fire("PostToolUse"), [])

    def test_patch_deletions_have_recorded_absence(self):
        path = self.write("a.py", "before")
        self.fire("PreToolUse")
        os.remove(path)
        row, = self.fire("PostToolUse")
        self.assertIsNone(row["after_hash"])

    def test_paths_outside_project_are_never_recorded(self):
        self.fire("PreToolUse", tool_input={"command": "*** Update File: ../outside.py"})
        self.assertEqual(self.fire("PostToolUse"), [])

    def test_provenance_excludes_credentials(self):
        self.write(".env", "PASSWORD=a")
        self.fire("PreToolUse", tool_input={"command": "*** Update File: .env"})
        self.write(".env", "PASSWORD=b")
        self.assertEqual(self.fire("PostToolUse"), [])

    def test_stop_records_completion_without_a_test_verdict(self):
        row, = self.fire("Stop")
        self.assertEqual(row["type"], "notice")
        self.assertEqual(row["message"], "Codex has finished its turn")
        self.assertNotIn("exit_code", row)

    def test_permission_notice_does_not_authorize_the_request(self):
        row, = self.fire("PermissionRequest")
        self.assertEqual(row["message"], "Codex is requesting your permission")

    def test_unstructured_shell_output_is_not_parsed_for_verdicts(self):
        self.assertEqual(self.fire("PostToolUse", tool_name="Bash", tool_response="all tests passed"), [])

    def test_explicit_shell_exit_code_is_recorded(self):
        row, = self.fire("PostToolUse", tool_name="Bash", tool_response={"exit_code": 1})
        self.assertEqual(row["exit_code"], 1)

    def test_codex_setup_preserves_notes_and_hooks_and_is_idempotent(self):
        self.write("AGENTS.md", "# Existing notes\nUse clear names.\n")
        self.write(".codex/hooks.json", '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"echo mine"}]}]}}')
        for _ in range(2):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(W.cmd_init([self.folder, "--agent", "codex", "--no-voice"]), 0)
        with open(os.path.join(self.folder, "AGENTS.md"), encoding="utf-8") as f:
            notes = f.read()
        self.assertIn("Use clear names", notes)
        self.assertEqual(notes.count("willow:start"), 1)
        self.assertFalse(os.path.exists(os.path.join(self.folder, "CLAUDE.md")))
        with open(os.path.join(self.folder, ".codex", "hooks.json"), encoding="utf-8") as f:
            config = json.load(f)
        self.assertEqual(len(config["hooks"]["Stop"]), 2)
        self.assertIn("--agent codex", json.dumps(config))

    def test_invalid_existing_codex_hooks_are_preserved(self):
        path = self.write(".codex/hooks.json", "not valid JSON")
        with self.assertRaises(ValueError):
            W._install_hooks(self.folder, "willow", agent="codex")
        with open(path) as f:
            self.assertEqual(f.read(), "not valid JSON")


if __name__ == "__main__":
    unittest.main()
