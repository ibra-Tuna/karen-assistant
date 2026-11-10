## Summary

<!-- One sentence describing what this PR does. -->

## What changed

<!-- A bullet list of the concrete changes. -->

- 
- 

## Why

<!-- The motivation. Link any related issue. -->

Closes #

## How was this tested?

<!--
Be specific. "Worked for me" is not enough. If you changed the agent, list
the exact commands you tried. If you changed the voice pipeline, describe
what you heard. If you changed the GUI, mention what you clicked.
-->

1. 
2. 
3. 

## Screenshots / logs

<!--
If this is a GUI change, include a before/after screenshot.
If this fixes a bug, include the relevant log excerpt from before the fix.
Redact any API keys.
-->

## Checklist

- [ ] I ran `python karen.py` and the app launches without errors
- [ ] I tested the feature end-to-end, not just the function in isolation
- [ ] No `karen_config.json`, `memory.json`, `groq_key.txt`, `models/`, or `dist/` files are staged (I checked `git status`)
- [ ] If I touched `toolkit.py`, the tool list in `SYSTEM_LOCAL` (`agent.py`) is updated
- [ ] If I changed user-facing behavior, `README.md` and/or `CHANGELOG.md` reflect it
- [ ] My commits are focused (one concern per commit)
- [ ] I read [`CONTRIBUTING.md`](../CONTRIBUTING.md)
