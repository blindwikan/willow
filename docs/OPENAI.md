# OpenAI models and Codex

Willow's model backend and its coding-agent integration are independent. The
core memory and factual announcements need neither an account nor a model.
The implementation follows the
[official OpenAI documentation for Responses](https://developers.openai.com/api/docs/guides/text),
[Codex hooks](https://learn.chatgpt.com/docs/hooks) and
[AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

## Optional OpenAI model backend

Set `OPENAI_API_KEY` in your shell, or place the key in
`~/.willow/openai_key` outside your project. Do not put API keys in project
settings, agent instructions or committed files.

```sh
willow watch --provider openai --narrate
```

This uses `/v1/responses` with instructions, text input, `max_output_tokens`,
and `store: false`. The setting controls API response storage; it is not a
claim of zero provider retention. Optional screen descriptions send an image
using `input_image`. Their existing automatic capture remains Windows-only.
Use `--model NAME` and `--vision-model NAME` to choose models available to your
account; Willow does not substitute another model when a request fails.

Willow extracts text from message outputs after any reasoning items. Refusals,
failed/incomplete responses, bad credentials and service errors fall back to
plain facts or an unavailable-description message. The existing narration time
budget still applies. Evidence-bearing memory descriptions always retain fixed
wording and bypass optional narration.

Legacy Chat Completions remains available:

```sh
willow watch --provider openai --openai-api chat-completions --narrate
```

For another server use `--provider openai-compat --base-url URL`, which keeps
Chat Completions. Willow uses Python's standard-library HTTP transport; the
OpenAI Python SDK is not a runtime dependency and need not be installed.
HTTP contract tests use a local fixture server, not a paid account. They do not
prove model availability or latency for a particular user's account.

## Codex coding-agent integration

From a Willow installation, set up the project:

```sh
willow init /path/to/project --agent codex --no-voice
```

Use quoted Windows paths where needed. Setup adds a marked section to
`AGENTS.md` and merges `.codex/hooks.json`. It preserves existing instructions
and valid hook entries and refuses to replace invalid existing hook JSON.
`--agent both` installs both Codex and Claude support; this is the default.
`--no-hooks` writes instructions without installing hooks.

Open the trusted project in a Codex version supporting these hooks, inspect
`/hooks`, and review/trust the new definitions. Willow does not change global
Codex configuration or grant permissions. `doctor` can inspect installed files
but cannot determine whether Codex has trusted their current definitions.

The instructions tell Codex to run builds and tests through `willow run`.
This captures actual exit codes even when agent tool output is unstructured.
The hooks observe `apply_patch` before and after a tool call, with a matching
tool-call ID and file hashes, and record changed file content as agent activity.
They also observe permission requests and turn completion. Hook output is
empty JSON; Willow never approves a request or asks Codex to continue a turn.

No-op/failed patches without changed bytes do not create write facts. Missing
pre-hooks, old records, private paths and paths outside the project do not
become agent-authorship claims. A matching observation is still not proof of
exclusive authorship when another process edits concurrently. Arbitrary shell
edits remain unknown unless separately instrumented or explicitly declared.
Willow does not parse the agent's prose to invent a test outcome or authorship.

Run `willow watch` in a separate terminal while Codex works. Prefer text-only
or on-demand speech alongside a screen reader. Codex hooks are an addition to
file watching, not a requirement for basic change awareness.
