# Working notes for Claude Code (Willow is watching this project)

Willow is a companion tool that narrates your work out loud to a blind
developer. Willow watches the files by itself, so you do not need to do anything
special for file changes.

**One rule:** so the developer can hear whether builds and tests actually pass
or fail, **run every build or test command through Willow**, like this:

    python "<PATH-TO>/willow.py" run <your command>

Examples (replace <PATH-TO> with the folder where willow.py lives):

    # Windows, e.g.:
    python "C:\Tools\Willow\willow.py" run npm test

    # Mac / Linux, e.g.:
    python "/Users/you/Willow/willow.py" run python -m pytest

Please do **not** run the raw command on its own — always wrap it with
`... willow.py run ...`. This captures the real pass/fail result (the true exit
code), so the developer hears an honest, verifiable outcome rather than your
description of it.

Everything else you do normally is fine.
