# Working notes for Claude Code (Willow is watching this project)

Willow is a companion tool that narrates your work out loud to a blind
developer. Willow watches the files by itself, so you do not need to do anything
special for file changes.

**One rule:** so the developer can hear whether builds and tests actually pass
or fail, **run every build or test command through Willow**:

    python willow.py run <your command>

For example:

    python willow.py run python -m unittest discover
    python willow.py run npm test

Please do **not** run the raw command on its own — always wrap it with
`willow run`. This captures the real exit code, so the developer hears an
honest, verifiable outcome rather than your description of it.

Everything else you do normally is fine.

---

## Notes specific to developing Willow itself

- **`RULES.md` is the specification, and it is tested.** Every rule on that page
  has a test in `test_willow.py` pinning the exact sentence it produces. If you
  change what Willow says, change `RULES.md` and its test together. A rulebook
  that describes behaviour the code doesn't have is a defect in the artifact,
  not just the docs.
- **Never let an unknown become a verdict.** If Willow cannot verify whether
  something passed or failed, it says it couldn't check, or says nothing. A
  command that failed to launch is not a failing test. This is the whole point
  of the tool.
- **Regression tests are named for what Willow used to say wrongly**, so keep
  that convention — it makes the reason a test exists obvious.
- **Standard library only.** No third-party dependencies, including for the
  optional model backends.
- Windows quirks worth knowing before touching `Speaker` or `Viewer`: a
  multi-line script passed to `powershell -Command` alongside a piped stdin kills
  the parent process; `[Console]::InputEncoding` throws when stdin is a pipe;
  and an inline base64 literal next to `FromBase64String` is blocked by AMSI as
  obfuscated code, which kills PowerShell silently. All three are commented at
  the places that avoid them.

Users of Willow get their own `CLAUDE.md` written for them by `willow init`,
with the correct absolute path already filled in — this one is hand-written
because the path is just `willow.py` in this repository.
