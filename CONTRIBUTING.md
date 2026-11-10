# Contributing to Karen Assistant

Thanks for taking the time to contribute. This document explains how to
report bugs, propose features, and submit pull requests.

---

## Reporting bugs

Before opening an issue:

1. Check the [existing issues](https://github.com/ibra-Tuna/karen-assistant/issues)
   — someone may have already reported the same thing.
2. Reproduce the bug on the latest `main`.
3. Collect the relevant lines from `karen.log` (next to the executable, or
   in `%APPDATA%\Karen\`). **Redact any API keys before pasting.**

When opening the issue, use the **Bug report** template. Include:

- What you did, what you expected, and what actually happened
- Your Windows version, Python version, and GPU (if local mode)
- Your brain mode (Cloud / Local / Hybrid)
- Any error messages or stack traces from `karen.log`

---

## Requesting features

Open an issue using the **Feature request** template. Describe:

- **The problem you're trying to solve.** This is more useful than a
  proposed solution — the maintainer may know a better path.
- **How you currently work around it**, if you do.
- **Any alternatives you've considered.**

Feature requests that are *"support macOS"* or *"add a mouse"* will be
closed — both are deliberate design decisions, not oversights.

---

## Development setup

```powershell
git clone https://github.com/ibra-Tuna/karen-assistant.git
cd karen-assistant

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

Smoke test before touching anything:

```powershell
python karen.py
```

You should see the GUI, hear nothing (Kokoro not yet downloaded), and the
welcome message asking for configuration.

---

## Project layout

| File | Role |
|---|---|
| `karen.py` | GUI, voice pipeline, brain, config, downloads |
| `agent.py` | Desktop agent loop — plans and executes tool calls |
| `toolkit.py` | The 68-tool surface (UIAutomation, files, shell, web, …) |
| `tasks.py` | Scheduled task parser and scheduler |

The public interfaces between these modules are stable. Changing the
schema of a tool in `toolkit.py` is a breaking change — please discuss it
in an issue first.

---

## Pull request checklist

- [ ] I ran `python karen.py` and the app launches without errors
- [ ] My change doesn't introduce new warnings or silent failures
- [ ] I tested the feature end-to-end (not just the function in isolation)
- [ ] No `karen_config.json`, `memory.json`, `groq_key.txt`, `models/`, or
      `dist/` files are staged (`git status` should be clean of those)
- [ ] If I touched `toolkit.py`, I updated `SYSTEM_LOCAL` in `agent.py` and
      the tool list in `README.md` if relevant
- [ ] My commit messages are descriptive ("Add volume_mute quick-action"
      beats "fix")

---

## Code style

There is no enforced linter — match the existing style. In short:

- 4-space indentation, no tabs.
- Functions return `str` for tool results, raising only for programmer
  errors.
- Log to `log.info` / `log.warning` / `log.exception`, not `print`.
- Guard every external call that could fail (network, subprocess, UIA) with
  a specific `try / except` and a user-readable error string.

The agent is deliberately strict about tool returns — if a tool returns a
string starting with one of the failure prefixes
(`"not found"`, `"failed:"`, `"denied"`, etc.), the loop counts it against
the 4-failure budget. Return a helpful string, not a stack trace.

---

## Security issues

**Do not** open a public issue for security vulnerabilities. Use GitHub's
private vulnerability reporting on the repo's **Security** tab. See
[`SECURITY.md`](SECURITY.md).

---

## License

By contributing, you agree that your contributions are licensed under the
same [MIT License](LICENSE) that covers the project.
