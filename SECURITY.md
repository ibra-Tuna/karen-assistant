# Security Policy

## What Karen can access

When **PC control** is enabled in Settings, Karen can:

- Read text from the active window via Windows UI Automation
- Invoke buttons, menu items, and other UI controls on your behalf
- Type into whatever has focus
- Press keyboard shortcuts
- Launch apps and open websites
- Capture the screen (only when **Read my screen** is also enabled)
- Periodically glance at your screen for context (only if **Screen glance**
  is on)

**Karen never moves the mouse.** All interaction goes through UI Automation
and keyboard input.

## What Karen does **not** do

- No network requests except to the LLM provider you configure, and to
  HuggingFace / GitHub for one-time model downloads.
- No telemetry, analytics, or crash reporting.
- No data is uploaded for model training.
- Conversation memory and long-term notes are stored locally in
  `memory.json` next to the executable (or in `%APPDATA%\Karen\` if the
  install folder is read-only).

## Sensitive window handling

When **Screen glance** is enabled, Karen skips windows matching a built-in
list: password managers, private/incognito browsing, and similar. The
match list lives in `ScreenGlance.SENSITIVE_PATTERNS` in `karen.py`.

Extensions to the list are welcome via pull request.

## How to report a vulnerability

**Do not open a public GitHub issue.**

Use GitHub's private vulnerability reporting on this repository:

1. Go to the **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the issue, its impact, and steps to reproduce.

You'll receive an acknowledgment within 72 hours. Please include:

- A description of the vulnerability and its severity
- Steps to reproduce (a minimal script is ideal)
- The affected file(s) and line number(s), if you know them
- Your disclosure preference (credit, anonymous, or co-ordination first)

## Supported versions

Only the latest commit on `main` receives security fixes. If you're running
a fork, pull the security commits from upstream.

## Out-of-scope

The following are not considered vulnerabilities:

- **Karen typing into an elevated window.** Windows prevents this by
  design. Run Karen as administrator if you need to target elevated apps.
- **API keys in `karen_config.json`.** The file is meant to be private.
  Do not commit it. If it's leaked, revoke the key at the provider.
- **The vision model describing content in a password manager.** Screen
  glance already skips these by default. If you've disabled the deny list,
  that's a user choice.

## Design intent

Karen is designed to be auditable: the entire permission surface is in
`toolkit.py`, and the agent's decision loop is in `agent.py`. If you can
read those two files, you know everything Karen can do.
