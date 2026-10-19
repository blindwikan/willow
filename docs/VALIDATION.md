# Validation of the follow-up build

Checked on 19 October 2026. Local execution was on Windows; this is not a
claim of native screen-reader validation on any platform.

| Interpreter used locally | Regression suite |
|---|---|
| Python 3.12.10 | 223 tests passed |
| Python 3.13.12 | 223 tests passed |
| Python 3.14.6 | 223 tests passed |
| Python 3.15.0 | 223 tests passed |

The Python 3.15 environment used pip 26.2.1, setuptools 84.2.0, wheel 0.48.0,
and build 1.6.1, resolved from PyPI during the check. OpenAI SDK 3.35.1 was
installed alongside Willow for coexistence; Willow's transport uses the
standard library and does not depend on that SDK. The OpenAI tests exercise
the Responses wire contract through a local HTTP server, including refusal,
incomplete output and service errors. No paid OpenAI request or real Codex
agent session was used for these tests.

Wheel and source distribution building passed with these tools on Python 3.15.
The source distribution includes the guides, rules, tests and evaluator.
Setuptools reports deprecation warnings for the existing legacy license-table
metadata and license classifier. These should be modernized with a deliberate
legacy-Python packaging policy before a future release requires it; the tested
build succeeds today.

The Python 3.15 synthetic evaluation passed 16 of 16 explicit fixtures, with
20 correct facts, no false positives/negatives and no repeated facts in those
fixtures. Across 110 small-project revisits it retained the configured 30
sessions; the database occupied 81,920 bytes. These are synthetic regression
results, not participant outcomes or realistic large-project performance claims.
The generated report records the host, source hash and measured timings.

CI is configured for current patch releases of Python 3.10–3.15 on Windows,
macOS and Linux, with legacy Python 3.8/3.9 jobs on Linux. It upgrades packaging
tools to their newest compatible versions, runs tests/evaluation, builds and
installs the distribution, and checks the installed entry point. Consult the
PR's checks for actual remote results; configuration alone does not establish
that the jobs passed.

Pending acceptance work: independent installation, native NVDA/JAWS/VoiceOver/
Orca speech and braille interaction, speech collisions, real Codex hook trust
and lifecycle behavior, real project traces, and participant evaluation.
Automatic screenshot capture remains Windows-only; optional legacy narration
has syntactic safeguards rather than a proof of semantic faithfulness.
