# Working on Willow

Willow supports blind developers. Read CLAUDE.md for the shared development
rules and RULES.md for the behavior specification.

- Run every build, test and technical evaluation through `python willow.py run`.
- Use only Python's standard library for runtime functionality.
- Keep uncertain outcomes and authorship explicit. A model's account of its
  work is not evidence that a command passed or that it authored file contents.
- Update the rulebook and meaningful regression tests when behavior changes.
- Preserve existing project settings and agent instructions during setup.
