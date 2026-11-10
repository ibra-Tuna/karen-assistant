# ==============================================================================
# toolkit.py — Unified tool surface for Karen's desktop agent.
#
# Keyboard + UI Automation only. No mouse control anywhere.
#   * click_element invokes the UIA InvokePattern/click on the element itself.
#     No coordinate fallback, no cursor movement.
#   * type_into verifies the value landed.
#   * wait_for_element / wait_for_window close the "launched but not ready" gap.
#   * dump_ui_tree marks the focused node so models resolve current context.
# ==============================================================================
from __future__ import annotations

import base64
import ctypes
import datetime
import fnmatch
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0


# ==============================================================================
# Low-level helpers (keyboard + screen capture only)
# ==============================================================================
def _uia():
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(1.2)
    return auto


def _pil():
    from PIL import Image
    return Image


def _grab(region: Optional[tuple] = None):
    Image = _pil()
    try:
        import mss
        with mss.mss() as sct:
            if region:
                l, t, w, h = region
                mon = {"left": int(l), "top": int(t), "width": int(w), "height": int(h)}
            else:
                mon = sct.monitors[0]
            shot = sct.grab(mon)
            return Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        from PIL import ImageGrab
        box = None
        if region:
            l, t, w, h = region
            box = (l, t, l + w, t + h)
        return ImageGrab.grab(bbox=box).convert("RGB")


def _send_unicode(text: str):
    from ctypes import wintypes as wt

    class KI(ctypes.Structure):
        _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                    ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MI(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class U(ctypes.Union):
        _fields_ = [("ki", KI), ("mi", MI)]

    class INP(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("u", U)]

    send = ctypes.windll.user32.SendInput
    send.argtypes = [wt.UINT, ctypes.POINTER(INP), ctypes.c_int]
    send.restype = wt.UINT
    UNICODE, UP = 0x4, 0x2

    for ch in text:
        if ch == "\n":
            _tap_vk(0x0D)
            continue
        if ch == "\t":
            _tap_vk(0x09)
            continue
        buf = ch.encode("utf-16-le")
        for i in range(0, len(buf), 2):
            code = int.from_bytes(buf[i:i + 2], "little")
            for flags in (UNICODE, UNICODE | UP):
                inp = INP(type=1)
                inp.u.ki = KI(0, code, flags, 0, 0)
                if send(1, ctypes.byref(inp), ctypes.sizeof(INP)) != 1:
                    raise RuntimeError("SendInput blocked (elevated window?)")
        time.sleep(0.012)


VK = {
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "enter": 0x0D, "esc": 0x1B,
    "tab": 0x09, "space": 0x20, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24, "delete": 0x2E,
    "backspace": 0x08, "insert": 0x2D,
    "media_play_pause": 0xB3, "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}
_ALIAS = {"control": "ctrl", "windows": "win", "super": "win", "escape": "esc",
          "return": "enter", "pgup": "pageup", "pgdn": "pagedown"}


def _tap_vk(vk: int):
    u = ctypes.windll.user32
    u.keybd_event(vk, 0, 0, 0)
    u.keybd_event(vk, 0, 2, 0)


def send_chord(keys: str) -> str:
    u = ctypes.windll.user32
    parts = [p for p in re.split(r"[,;]\s*", keys) if p.strip()]
    done = []
    for part in parts:
        toks = [_ALIAS.get(t.strip().lower(), t.strip().lower())
                for t in part.split("+") if t.strip()]
        vks = []
        for t in toks:
            if t in VK:
                vks.append(VK[t])
            elif len(t) == 1:
                vks.append(ord(t.upper()))
            else:
                return f"unknown key: {t}"
        if vks == [0x11, 0x12, 0x2E]:
            return "refused: ctrl+alt+del"
        for vk in vks:
            u.keybd_event(vk, 0, 0, 0)
        for vk in reversed(vks):
            u.keybd_event(vk, 0, 2, 0)
        done.append(part)
        time.sleep(0.06)
    return "pressed " + ", ".join(done)


def _rect(ctrl) -> tuple:
    try:
        r = ctrl.BoundingRectangle
        return (int(r.left), int(r.top), int(r.width()), int(r.height()))
    except Exception:
        return (0, 0, 0, 0)


def _describe(ctrl) -> dict:
    def safe(attr, default=""):
        try:
            v = getattr(ctrl, attr)
            return v if v is not None else default
        except Exception:
            return default
    return {
        "role": safe("ControlTypeName", "Control"),
        "name": str(safe("Name", ""))[:120],
        "automation_id": str(safe("AutomationId", "")),
        "class_name": str(safe("ClassName", "")),
        "rect": _rect(ctrl),
        "enabled": bool(safe("IsEnabled", True)),
        "offscreen": bool(safe("IsOffscreen", False)),
    }


def _matches(ctrl, spec: dict) -> bool:
    if not any(spec.get(k) for k in ("automation_id", "control_type", "class_name", "name")):
        return False
    d = _describe(ctrl)
    if d["offscreen"] or not d["enabled"]:
        return False
    if spec.get("automation_id") and d["automation_id"] != spec["automation_id"]:
        return False
    if spec.get("control_type") and d["role"].lower() != spec["control_type"].lower():
        return False
    if spec.get("class_name") and d["class_name"].lower() != spec["class_name"].lower():
        return False
    name = spec.get("name")
    if name:
        n = d["name"].lower()
        want = name.lower()
        if not n or not (n == want or want in n or (len(n) >= 4 and n in want)):
            return False
    return True


# ==============================================================================
# WinTools
# ==============================================================================
class WinTools:
    INTERACTIVE_ROLES = {
        "ButtonControl", "EditControl", "MenuItemControl", "HyperlinkControl",
        "TabItemControl", "CheckBoxControl", "ComboBoxControl", "ListItemControl",
        "RadioButtonControl", "TreeItemControl", "SplitButtonControl", "SliderControl",
    }

    def __init__(self, approve: Callable[[str, str], bool]):
        self.approve = approve
        self._cache: Dict[int, Any] = {}
        self._last_dump_window = ""
        self._last_dump_time = 0.0

    # ---------- windows ------------------------------------------------
    def list_windows(self, limit: int = 30) -> str:
        auto = _uia()
        out = []
        for w in auto.GetRootControl().GetChildren():
            d = _describe(w)
            if d["role"] != "WindowControl" or not d["name"] or d["offscreen"]:
                continue
            out.append(f"{d['name'][:70]}  [{d['class_name']}]  {d['rect'][2]}x{d['rect'][3]}")
            if len(out) >= limit:
                break
        return "\n".join(out) or "no visible windows"

    def _window(self, title: str):
        auto = _uia()
        want = (title or "").lower().strip()
        if not want:
            return self._foreground()
        best = None
        for w in auto.GetRootControl().GetChildren():
            d = _describe(w)
            if d["role"] != "WindowControl" or d["offscreen"]:
                continue
            n = d["name"].lower()
            if n == want:
                return w
            if best is None and want in n:
                best = w
            elif best is None and n and n in want:
                best = w
        return best

    def _foreground(self):
        auto = _uia()
        try:
            return auto.GetForegroundControl()
        except Exception:
            return auto.GetRootControl()

    def focus_window(self, title: str) -> str:
        w = self._window(title)
        if w is None:
            return f"no window matching {title!r} — call list_windows"
        try:
            w.SetActive()
            w.SetFocus()
        except Exception as e:
            return f"could not focus: {e}"
        time.sleep(0.25)
        return f"focused {_describe(w)['name'][:70]!r}"

    def wait_for_window(self, title: str = "", timeout: float = 5.0) -> str:
        if not title:
            return "title required"
        try:
            timeout = max(0.5, min(30.0, float(timeout)))
        except (TypeError, ValueError):
            timeout = 5.0
        t0 = time.time()
        while time.time() - t0 < timeout:
            w = self._window(title)
            if w is not None:
                return f"window {title!r} appeared after {time.time() - t0:.1f}s"
            time.sleep(0.2)
        return f"window {title!r} did not appear within {timeout:.0f}s"

    def window_op(self, title: str, op: str) -> str:
        w = self._window(title)
        if w is None:
            return f"no window matching {title!r}"
        op = op.lower()
        try:
            if op == "minimize":
                w.Minimize()
            elif op == "maximize":
                w.Maximize()
            elif op == "restore":
                w.Restore()
            elif op == "close":
                if not self.approve(f"Close window {title!r}", "window close"):
                    return "denied"
                w.Close()
            elif op == "focus":
                w.SetActive()
            else:
                return "op must be focus|minimize|maximize|restore|close"
        except Exception as e:
            return f"{op} failed: {e}"
        time.sleep(0.2)
        return f"{op} ok on {title!r}"

    def launch_app(self, name: str, wait_s: float = 8.0) -> str:
        known = {
            "calculator": "calc.exe", "calc": "calc.exe", "notepad": "notepad.exe",
            "paint": "mspaint.exe", "explorer": "explorer.exe", "files": "explorer.exe",
            "file explorer": "explorer.exe", "cmd": "cmd.exe", "terminal": "wt.exe",
            "command prompt": "cmd.exe", "powershell": "powershell.exe",
            "task manager": "taskmgr.exe", "control panel": "control.exe",
            "settings": "ms-settings:", "store": "ms-windows-store:",
        }
        target = known.get(name.strip().lower(), name.strip())
        stem = Path(target).stem if "." in target and not target.startswith("ms-") else target
        if not target.startswith(("ms-", "shell:")) and self._window(stem) is not None:
            return self.focus_window(stem)
        try:
            if target.startswith("ms-") or target.startswith("shell:"):
                os.startfile(target)
            else:
                subprocess.Popen(["cmd", "/c", "start", "", target],
                                 creationflags=NO_WINDOW, shell=False)
        except Exception:
            return f"could not start {name!r}"
        deadline = time.time() + wait_s
        while time.time() < deadline:
            time.sleep(0.4)
            if self._window(Path(target).stem) or self._window(name):
                time.sleep(0.6)
                return f"launched {name!r}"
        return f"launched {name!r} (no window matched yet)"

    # ---------- tree dump ---------------------------------------------
    def dump_ui_tree(self, window: str = "", max_depth: int = 6, max_nodes: int = 220) -> str:
        auto = _uia()
        root = self._window(window) if window else self._foreground()
        if root is None:
            return "no window to dump"
        try:
            focused = auto.GetFocusedControl()
        except Exception:
            focused = None
        self._cache.clear()
        self._last_dump_time = time.time()
        lines: List[str] = []
        i = 0

        def walk(c, depth):
            nonlocal i
            if depth > max_depth or i >= max_nodes:
                return
            d = _describe(c)
            if d["offscreen"]:
                return
            i += 1
            self._cache[i] = c
            focus_mark = "*" if focused is not None and c is focused else " "
            interactive = ">" if d["role"] in self.INTERACTIVE_ROLES else " "
            label = (f"#{i:<3}{focus_mark}{interactive} " + "  " * depth
                     + f"{d['role']} name={d['name']!r} id={d['automation_id']!r}")
            if d["class_name"]:
                label += f" class={d['class_name']!r}"
            if not d["enabled"]:
                label += " [disabled]"
            lines.append(label)
            try:
                kids = c.GetChildren()
            except Exception:
                kids = []
            for k in kids:
                walk(k, depth + 1)

        walk(root, 0)
        self._last_dump_window = _describe(root)["name"]
        legend = "  (* = focused, > = interactive)\n"
        head = f"UI tree of {self._last_dump_window[:60]!r} ({i} nodes). Use #N as element index.\n{legend}"
        return head + "\n".join(lines)

    # ---------- find helpers ------------------------------------------
    def _find_walk(self, spec: dict):
        auto = _uia()
        roots = []
        if spec.get("window"):
            w = self._window(spec["window"])
            if w is not None:
                roots.append(w)
        roots.append(self._foreground())
        roots.append(auto.GetRootControl())
        for root in roots:
            stack = [(root, 0)]
            while stack:
                c, depth = stack.pop()
                if depth > 12:
                    continue
                try:
                    if _matches(c, spec):
                        return c
                except Exception:
                    pass
                try:
                    kids = c.GetChildren()
                except Exception:
                    kids = []
                for k in reversed(kids):
                    stack.append((k, depth + 1))
        return None

    def _find(self, spec: dict, retry: bool = True):
        if spec.get("index"):
            return self._cache.get(spec["index"])
        c = self._find_walk(spec)
        if c is None and retry:
            time.sleep(0.12)
            c = self._find_walk(spec)
        return c

    def _wait_for(self, spec: dict, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self._find(spec, retry=False) is not None:
                return True
            time.sleep(0.15)
        return False

    def wait_for_element(self, window: str = "", name: str = "", automation_id: str = "",
                         control_type: str = "", timeout: float = 5.0) -> str:
        try:
            timeout = max(0.5, min(30.0, float(timeout)))
        except (TypeError, ValueError):
            timeout = 5.0
        spec = {"window": window, "name": name, "automation_id": automation_id,
                "control_type": control_type}
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._find(spec, retry=False) is not None:
                return f"element {name or automation_id or control_type!r} appeared after {time.time() - t0:.1f}s"
            time.sleep(0.15)
        return f"element not found within {timeout:.0f}s — call dump_ui_tree to see the current tree"

    def find_element(self, window: str = "", name: str = "", automation_id: str = "",
                     control_type: str = "", class_name: str = "") -> str:
        spec = {"window": window, "name": name, "automation_id": automation_id,
                "control_type": control_type, "class_name": class_name}
        c = self._find(spec)
        if c is None:
            return "not found — re-dump the UI tree and pick a stable identifier"
        d = _describe(c)
        return (f"found {d['role']} name={d['name']!r} id={d['automation_id']!r} "
                f"rect={d['rect']}")

    # ---------- click -------------------------------------------------
    def click_element(self, index: int = 0, window: str = "", name: str = "",
                      automation_id: str = "", control_type: str = "",
                      verify_name: str = "", verify_timeout: float = 0.0) -> str:
        # Stale-cache detection
        if index and self._last_dump_time and (time.time() - self._last_dump_time) > 8.0:
            self._cache.clear()
            self._last_dump_time = 0.0
            return (f"stale: cache from #{index} is more than 8s old. "
                    "Re-dump the UI tree before clicking.")

        spec = {"index": index, "window": window, "name": name,
                "automation_id": automation_id, "control_type": control_type}
        c = self._find(spec)
        if c is None and not index and (name or automation_id or control_type):
            c = self._find(spec, retry=True)
        if c is None:
            if index:
                return f"element #{index} is stale or no longer visible — re-dump the UI tree"
            return "element not found — re-dump the UI tree"

        try:
            _ = c.BoundingRectangle
        except Exception:
            return "control is stale (window changed) — re-dump the UI tree"

        d = _describe(c)
        if d["offscreen"]:
            return f"{d['name'] or d['role']!r} is off-screen — scroll or re-dump"
        if not d["enabled"]:
            return f"{d['name'] or d['role']!r} is disabled — a prerequisite action is probably missing"

        before = _describe(self._foreground())["name"]
        try:
            c.Click()
        except Exception as e:
            # UIA-only: no coordinate fallback, no cursor movement.
            return (f"UIA click failed on {d['name'] or d['role']!r}: {str(e)[:80]}. "
                    f"Try press_keys with a shortcut, or click a different identifier.")

        if verify_name or verify_timeout:
            ok = self._wait_for({"name": verify_name} if verify_name else spec,
                                verify_timeout or 2.0)
            return ("clicked; verified " + (verify_name or "same element")) if ok \
                else "clicked but verification failed — re-dump the UI tree"
        time.sleep(0.2)
        after = _describe(self._foreground())["name"]
        change = "" if after == before else f"; foreground is now {after[:50]!r}"
        return f"clicked {d['name'] or d['role']!r}{change}"

    # ---------- type --------------------------------------------------
    def type_into(self, text: str, index: int = 0, window: str = "", name: str = "",
                  automation_id: str = "", submit=False) -> str:
        submit = str(submit).strip().lower() in ("1", "true", "yes", "y", "on")
        c = self._find({"index": index, "window": window, "name": name,
                        "automation_id": automation_id})
        if c is None:
            return "field not found — re-dump the UI tree"
        try:
            c.SetFocus()
        except Exception:
            pass
        wrote = False
        try:
            c.GetValuePattern().SetValue(text)
            wrote = True
        except Exception:
            pass
        if not wrote:
            try:
                c.SendKeys(text, waitTime=0.01)
                wrote = True
            except Exception:
                _send_unicode(text)
                wrote = True
        if submit:
            send_chord("enter")
            time.sleep(0.4)
            return "typed and submitted"
        try:
            got = c.GetValuePattern().Value
            if text.strip()[:20] in (got or ""):
                return f"typed; value now {got[:60]!r}"
            return f"typed but value does not match (got {got[:60]!r})"
        except Exception:
            return "typed (could not verify)"

    def type_text(self, text: str, submit: bool = False) -> str:
        """Type into whatever control currently has focus.
        No element lookup — works for 'type hello' without a #index."""
        text = (text or "")[:2000]
        if not text:
            return "no text to type"
        submit = str(submit).strip().lower() in ("1", "true", "yes", "y", "on")

        try:
            auto = _uia()
            fg = auto.GetForegroundControl()
            fg_name = _describe(fg).get("name", "")[:40] if fg else "(focused window)"
        except Exception:
            auto = None
            fg_name = "(focused window)"

        # 1. UIA SendKeys on the focused element — respects app text handling
        if auto is not None:
            try:
                focused = auto.GetFocusedControl()
                if focused is not None:
                    focused.SendKeys(text, waitTime=0.01)
                    if submit:
                        send_chord("enter")
                        time.sleep(0.3)
                        return f"typed and submitted ({fg_name})"
                    return f"typed into {fg_name!r}"
            except Exception:
                pass

        # 2. Raw SendInput unicode — works for most non-UIA apps
        try:
            _send_unicode(text)
            if submit:
                send_chord("enter")
                time.sleep(0.3)
                return f"typed and submitted ({fg_name}) via SendInput"
            return f"typed into {fg_name!r} via SendInput"
        except Exception as e:
            return (f"typing failed: {str(e)[:80]}. "
                    "If the target is elevated, run Karen as admin.")

    def read_text(self, window: str = "", max_chars: int = 4000) -> str:
        root = self._window(window) if window else self._foreground()
        if root is None:
            return "no window"
        names: List[str] = []
        total = [0]

        def walk(c, depth):
            if depth > 8 or total[0] > max_chars:
                return
            d = _describe(c)
            if d["name"] and not d["offscreen"]:
                names.append(d["name"])
                total[0] += len(d["name"])
            try:
                for k in c.GetChildren():
                    walk(k, depth + 1)
            except Exception:
                pass

        walk(root, 0)
        text = "\n".join(dict.fromkeys(names))
        if text.strip():
            return text[:max_chars]
        try:
            import pytesseract
            return pytesseract.image_to_string(_grab())[:max_chars]
        except Exception:
            return "(no text exposed by this app; screenshot may be needed)"

    # ---------- screenshot --------------------------------------------
    def screenshot(self, window: str = "", path: str = "") -> str:
        img = _grab()
        if window:
            w = self._window(window)
            if w is not None:
                x, y, ww, hh = _rect(w)
                if ww and hh:
                    img = _grab((x, y, ww, hh))
        out = path or str(Path.home() / "Pictures" / "Karen" /
                          f"shot_{time.strftime('%Y%m%d_%H%M%S')}.png")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        return out

    def screenshot_region(self, x: int, y: int, w: int, h: int, path: str = "") -> str:
        img = _grab((int(x), int(y), int(w), int(h)))
        out = path or str(Path.home() / "Pictures" / "Karen" /
                          f"region_{time.strftime('%Y%m%d_%H%M%S')}.png")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        return out

    def snap(self, title: str, position: str) -> str:
        r = self.focus_window(title)
        if r.startswith(("no window", "could not")):
            return r
        time.sleep(0.2)
        pos = (position or "").lower()
        key = {
            "left": "win+left", "right": "win+right",
            "up": "win+up", "down": "win+down",
            "maximize": "win+up", "max": "win+up",
        }.get(pos)
        if not key:
            return f"unknown position: {position}"
        return f"snapped {title!r} {position} ({send_chord(key)})"

    def wait(self, seconds: float) -> str:
        time.sleep(max(0.2, min(20.0, float(seconds))))
        return f"waited {seconds}s"

    def peek(self, max_w: int = 640) -> Any:
        img = _grab()
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, max(1, int(img.height * ratio))))
        return img

    # ---------- chat reply --------------------------------------------
    def reply_message(self, text: str, app: str = "", contact: str = "", send=True) -> str:
        text = re.sub(r"\s*\n\s*", " ", (text or "").strip())
        if not text:
            return "failed: empty message"
        send = str(send).strip().lower() not in ("false", "0", "no", "")
        if app:
            r = self.focus_window(app)
            if r.startswith(("no window", "could not")):
                return r
        if contact:
            item = self._find({"window": app, "name": contact, "control_type": "ListItemControl"})
            if item is None:
                return (f"contact {contact!r} not found in the chat list — "
                        "dump_ui_tree and pick the conversation, then call reply_message without contact")
            try:
                item.Click()
            except Exception:
                pass
            time.sleep(0.6)
        box = self._chat_input(app)
        if box is None:
            return "message input not found — dump_ui_tree and use type_into on the message box"
        self._enter_text(box, text)
        who = contact or app or _describe(self._foreground())["name"][:40]
        if not send:
            return f"drafted in {who!r}, not sent"
        if not self.approve(f"Send this message to {who}?", text[:300]):
            return f"drafted in {who!r}; denied, not sent"
        send_chord("enter")
        time.sleep(0.3)
        return f"sent to {who!r}: {text[:60]!r}"

    _INPUT_HINTS = ("message", "type a", "reply", "write", "compose", "say something")

    def _chat_input(self, window: str = ""):
        root = self._window(window) if window else self._foreground()
        if root is None:
            return None
        hinted, lowest, lowest_y = None, None, -1
        stack = [(root, 0)]
        while stack:
            c, depth = stack.pop()
            if depth > 14:
                continue
            d = _describe(c)
            if d["offscreen"]:
                continue
            if d["role"] == "EditControl" and d["enabled"]:
                label = (d["name"] + " " + d["automation_id"]).lower()
                if hinted is None and any(h in label for h in self._INPUT_HINTS):
                    hinted = c
                if d["rect"][1] > lowest_y:
                    lowest, lowest_y = c, d["rect"][1]
            try:
                kids = c.GetChildren()
            except Exception:
                kids = []
            for k in kids:
                stack.append((k, depth + 1))
        return hinted or lowest

    def _enter_text(self, c, text: str):
        try:
            c.Click()
        except Exception:
            try:
                c.SetFocus()
            except Exception:
                pass
        time.sleep(0.15)
        try:
            vp = c.GetValuePattern()
            vp.SetValue(text)
            if text[:20] in (vp.Value or ""):
                return
        except Exception:
            pass
        _send_unicode(text)


# ==============================================================================
# FileTools
# ==============================================================================
class FileTools:
    def __init__(self, approve, allowed: Optional[List[str]] = None):
        self.approve = approve
        home = Path.home()
        self.allowed = [Path(p).expanduser().resolve()
                        for p in (allowed or [home / "Documents", home / "Downloads",
                                              home / "Desktop", Path.cwd()])]

    def _safe(self, p: str) -> Path:
        path = Path(p).expanduser().resolve()
        if not any(path == a or a in path.parents for a in self.allowed):
            raise PermissionError(f"{path} is outside the allowed folders")
        return path

    def list_dir(self, path: str = "~", pattern: str = "*") -> str:
        p = self._safe(path)
        rows = []
        for e in sorted(p.iterdir()):
            if not fnmatch.fnmatch(e.name, pattern):
                continue
            size = e.stat().st_size if e.is_file() else 0
            rows.append(f"{'d' if e.is_dir() else 'f'} {e.name}  {size}")
            if len(rows) >= 200:
                break
        return "\n".join(rows) or "(empty)"

    def read_file(self, path: str, offset: int = 0, limit: int = 400) -> str:
        p = self._safe(path)
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        chunk = lines[offset:offset + limit]
        return "\n".join(f"{offset + i + 1:5} {l}" for i, l in enumerate(chunk)) or "(empty)"

    def write_file(self, path: str, content: str) -> str:
        p = self._safe(path)
        if p.exists() and not self.approve(f"Overwrite {p.name}", str(p)):
            return "denied"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {p}"

    def append_file(self, path: str, content: str) -> str:
        p = self._safe(path)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return f"appended {len(content)} chars to {p}"

    def edit_file(self, path: str, find: str, replace: str, count: int = 0) -> str:
        p = self._safe(path)
        text = p.read_text(encoding="utf-8")
        n = text.count(find)
        if n == 0:
            return "find string not present"
        if not self.approve(f"Edit {p.name} ({n} occurrence(s))", str(p)):
            return "denied"
        p.write_text(text.replace(find, replace, count or -1), encoding="utf-8")
        return f"replaced {n if not count else min(n, count)} occurrence(s)"

    def move(self, src: str, dst: str) -> str:
        s, d = self._safe(src), self._safe(dst)
        if not self.approve(f"Move {s.name} → {d}", f"{s} -> {d}"):
            return "denied"
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return f"moved to {d}"

    def copy(self, src: str, dst: str) -> str:
        s, d = self._safe(src), self._safe(dst)
        if not self.approve(f"Copy {s.name} → {d}", f"{s} -> {d}"):
            return "denied"
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(str(s), str(d), dirs_exist_ok=True)
        else:
            shutil.copy2(str(s), str(d))
        return f"copied to {d}"

    def mkdir(self, path: str) -> str:
        p = self._safe(path)
        if p.exists():
            return f"already exists: {p}"
        p.mkdir(parents=True, exist_ok=True)
        return f"created {p}"

    def delete(self, path: str) -> str:
        p = self._safe(path)
        if not self.approve(f"Delete {p.name}", str(p)):
            return "denied"
        shutil.rmtree(p) if p.is_dir() else p.unlink()
        return f"deleted {p}"

    def info(self, path: str) -> str:
        p = self._safe(path)
        if not p.exists():
            return f"not found: {p}"
        st = p.stat()
        kind = "dir" if p.is_dir() else "file"
        return (f"path: {p}\n"
                f"type: {kind}\n"
                f"size: {st.st_size} bytes\n"
                f"modified: {datetime.datetime.fromtimestamp(st.st_mtime)}\n"
                f"created: {datetime.datetime.fromtimestamp(st.st_ctime)}")

    def find_by_name(self, pattern: str, path: str = "~", limit: int = 50) -> str:
        root = self._safe(path)
        hits = []
        for f in root.rglob(pattern):
            hits.append(str(f))
            if len(hits) >= limit:
                break
        return "\n".join(hits) or "no matches"

    def search(self, pattern: str, path: str = "~", glob: str = "*") -> str:
        root = self._safe(path)
        hits = []
        for f in root.rglob(glob):
            if not f.is_file() or f.stat().st_size > 2_000_000:
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8",
                                                    errors="ignore").splitlines(), 1):
                    if pattern in line:
                        hits.append(f"{f}:{i}: {line.strip()[:120]}")
                        if len(hits) >= 80:
                            return "\n".join(hits)
            except Exception:
                continue
        return "\n".join(hits) or "no matches"


# ==============================================================================
# ShellTools
# ==============================================================================
class ShellTools:
    DANGER = re.compile(
        r"\b(rm\s+-rf|rmdir\s+/s|del\s+/[fq]|format|diskpart|reg\s+delete|"
        r"shutdown|bcdedit|takeown|icacls|vssadmin)\b", re.I)

    def __init__(self, approve):
        self.approve = approve

    def run(self, command: str, cwd: str = "", timeout: int = 60) -> str:
        if self.DANGER.search(command) and not self.approve("Run destructive command", command):
            return "denied"
        try:
            r = subprocess.run(command, shell=True, cwd=cwd or None,
                               capture_output=True, timeout=timeout,
                               creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return f"timed out after {timeout}s"
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        parts = [f"exit {r.returncode}"]
        if out:
            parts.append("stdout:\n" + out[-3000:])
        if err:
            parts.append("stderr:\n" + err[-1500:])
        return "\n".join(parts)

    def run_ps(self, command: str, timeout: int = 60) -> str:
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return f"timed out after {timeout}s"
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        parts = [f"exit {r.returncode}"]
        if out:
            parts.append("stdout:\n" + out[-3000:])
        if err:
            parts.append("stderr:\n" + err[-1500:])
        return "\n".join(parts)


# ==============================================================================
# WebTools
# ==============================================================================
class WebTools:
    SITES = {
        "google": "https://www.google.com", "youtube": "https://www.youtube.com",
        "gmail": "https://mail.google.com", "github": "https://github.com",
        "reddit": "https://www.reddit.com", "facebook": "https://www.facebook.com",
        "instagram": "https://www.instagram.com", "twitter": "https://x.com", "x": "https://x.com",
        "linkedin": "https://www.linkedin.com", "netflix": "https://www.netflix.com",
        "amazon": "https://www.amazon.com", "wikipedia": "https://www.wikipedia.org",
        "chatgpt": "https://chatgpt.com", "maps": "https://maps.google.com",
        "google maps": "https://maps.google.com", "drive": "https://drive.google.com",
        "google drive": "https://drive.google.com", "docs": "https://docs.google.com",
        "calendar": "https://calendar.google.com", "translate": "https://translate.google.com",
        "stackoverflow": "https://stackoverflow.com", "twitch": "https://www.twitch.tv",
        "spotify": "https://open.spotify.com", "discord": "https://discord.com/app",
        "whatsapp": "https://web.whatsapp.com", "telegram": "https://web.telegram.org",
        "outlook": "https://outlook.live.com", "news": "https://news.google.com",
    }
    WEB_FIRST = {"google", "youtube", "gmail", "github", "reddit", "facebook", "instagram", "twitter",
                 "x", "linkedin", "netflix", "amazon", "wikipedia", "chatgpt", "maps", "google maps",
                 "drive", "google drive", "docs", "calendar", "translate", "stackoverflow", "news"}
    ENGINES = {
        "google": "https://www.google.com/search?q=",
        "images": "https://www.google.com/search?tbm=isch&q=",
        "youtube": "https://www.youtube.com/results?search_query=",
        "maps": "https://www.google.com/maps/search/",
        "bing": "https://www.bing.com/search?q=",
        "duckduckgo": "https://duckduckgo.com/?q=",
        "wikipedia": "https://en.wikipedia.org/w/index.php?search=",
    }
    _DOMAIN = re.compile(r"^(?:www\.)?[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.[a-z]{2,}"
                         r"(?::\d+)?(?:[/?#]\S*)?$", re.I)
    _FILE_EXT = {"exe", "bat", "cmd", "msi", "lnk", "dll", "py", "txt", "ps1", "json", "md", "pdf",
                 "doc", "docx", "xls", "xlsx", "zip", "ini", "log"}

    @classmethod
    def resolve(cls, target: str):
        t = (target or "").strip().strip("\"'<>").strip()
        if not t:
            return None
        if re.match(r"^https?://\S+$", t, re.I):
            return t
        if re.match(r"^[a-z][a-z0-9+.\-]*:", t, re.I) and not re.match(r"^[\w.\-]+:\d+", t):
            return None
        if " " not in t and cls._DOMAIN.match(t):
            host = re.split(r"[/?#:]", t, 1)[0]
            if host.rsplit(".", 1)[-1].lower() in cls._FILE_EXT:
                return None
            return "https://" + t
        key = re.sub(r"\s+(?:website|web ?site|site|web ?page|page)$", "", t.lower()).strip()
        return cls.SITES.get(key)

    @classmethod
    def is_web_first(cls, target: str) -> bool:
        t = (target or "").strip().lower()
        return bool(cls._DOMAIN.match(t)) or t in cls.WEB_FIRST

    @classmethod
    def open_site(cls, target: str) -> str:
        url = cls.resolve(target)
        if not url:
            return f"not a website I know: {target!r} - give a full URL or domain"
        webbrowser.open_new_tab(url)
        return f"opened {url}"

    @classmethod
    def search(cls, query: str, engine: str = "google") -> str:
        q = (query or "").strip()
        if not q:
            return "failed: empty query"
        name = (engine or "google").strip().lower()
        base = cls.ENGINES.get(name, cls.ENGINES["google"])
        webbrowser.open_new_tab(base + urllib.parse.quote_plus(q))
        return f"searched {name if name in cls.ENGINES else 'google'} (results opened in the browser)"

    @staticmethod
    def fetch(url: str, max_chars: int = 6000) -> str:
        try:
            import httpx
            r = httpx.get(url, timeout=15, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (KarenAgent)"})
            text = r.text
            text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:max_chars]
        except Exception as e:
            return f"fetch failed: {e}"

    @staticmethod
    def open_url(url: str) -> str:
        return WebTools.open_site(url)


# ==============================================================================
# SystemTools
# ==============================================================================
class SystemTools:
    def __init__(self, approve):
        self.approve = approve

    def stats(self) -> str:
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.3)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            lines = [
                f"CPU: {cpu:.0f}%  ({psutil.cpu_count(logical=False)} cores / {psutil.cpu_count()} threads)",
                f"RAM: {mem.percent:.0f}% ({mem.used / 1e9:.1f} / {mem.total / 1e9:.1f} GB)",
                f"Disk (C:): {disk.percent:.0f}% ({disk.used / 1e9:.1f} / {disk.total / 1e9:.1f} GB)",
            ]
            b = psutil.sensors_battery()
            if b:
                lines.append(f"Battery: {b.percent:.0f}%{' (charging)' if b.power_plugged else ''}")
            return "\n".join(lines)
        except Exception as e:
            return f"failed: {e}"

    def battery(self) -> str:
        try:
            import psutil
            b = psutil.sensors_battery()
            if not b:
                return "no battery detected"
            return f"battery: {b.percent:.0f}%{' (charging)' if b.power_plugged else ''}"
        except Exception as e:
            return f"failed: {e}"

    def disk_usage(self, path: str = "C:") -> str:
        try:
            import psutil
            d = psutil.disk_usage(path)
            return (f"{path}: {d.percent:.0f}% used "
                    f"({d.used / 1e9:.1f} / {d.total / 1e9:.1f} GB free: {d.free / 1e9:.1f} GB)")
        except Exception as e:
            return f"failed: {e}"

    def list_processes(self, sort_by: str = "cpu", limit: int = 20) -> str:
        try:
            import psutil
            procs = []
            for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                try:
                    procs.append(dict(p.info))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            time.sleep(0.15)
            for p in psutil.process_iter(['pid', 'cpu_percent']):
                try:
                    info = next((x for x in procs if x['pid'] == p.info['pid']), None)
                    if info:
                        info['cpu_percent'] = p.info['cpu_percent']
                except Exception:
                    pass
            if sort_by == "mem":
                procs.sort(key=lambda x: x.get('memory_percent') or 0, reverse=True)
            else:
                procs.sort(key=lambda x: x.get('cpu_percent') or 0, reverse=True)
            lines = [f"{p['pid']:>6}  {(p.get('name') or '?')[:30]:<30}  "
                     f"cpu {p.get('cpu_percent') or 0:>5.1f}%  mem {p.get('memory_percent') or 0:>4.1f}%"
                     for p in procs[:limit]]
            return "\n".join(lines) or "no processes"
        except Exception as e:
            return f"failed: {e}"

    def kill_process(self, pid_or_name) -> str:
        try:
            import psutil
            s = str(pid_or_name)
            if s.isdigit():
                pid = int(s)
                try:
                    p = psutil.Process(pid)
                    name = p.name()
                except psutil.NoSuchProcess:
                    return f"no process with pid {pid}"
                if not self.approve(f"Kill process {name} (pid {pid})?", ""):
                    return "denied"
                p.terminate()
                try:
                    p.wait(timeout=3)
                except psutil.TimeoutExpired:
                    p.kill()
                return f"terminated {name} (pid {pid})"
            killed = []
            for p in psutil.process_iter(['name']):
                try:
                    if s.lower() in (p.info['name'] or '').lower():
                        if self.approve(f"Kill {p.info['name']} (pid {p.pid})?", ""):
                            p.terminate()
                            killed.append(p.info['name'])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if not killed:
                return f"no process matching {s!r}"
            return f"terminated {len(killed)} process(es)"
        except Exception as e:
            return f"failed: {e}"

    def _get_volume_iface(self):
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        devices = AudioUtilities.GetSpeakers()
        return cast(devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
                    POINTER(IAudioEndpointVolume))

    def volume_get(self) -> str:
        try:
            v = self._get_volume_iface()
            pct = int(round(v.GetMasterVolumeLevelScalar() * 100))
            muted = bool(v.GetMute())
            return f"volume: {pct}%{' (muted)' if muted else ''}"
        except ImportError:
            return "volume control needs pycaw (pip install pycaw)"
        except Exception as e:
            return f"failed: {e}"

    def volume_set(self, level: int) -> str:
        try:
            level = max(0, min(100, int(level)))
            v = self._get_volume_iface()
            v.SetMasterVolumeLevelScalar(level / 100.0, None)
            return f"volume set to {level}%"
        except ImportError:
            return "volume control needs pycaw (pip install pycaw)"
        except Exception as e:
            return f"failed: {e}"

    def volume_mute(self) -> str:
        try:
            v = self._get_volume_iface()
            new = not bool(v.GetMute())
            v.SetMute(new, None)
            return f"{'muted' if new else 'unmuted'}"
        except ImportError:
            return "volume control needs pycaw (pip install pycaw)"
        except Exception as e:
            return f"failed: {e}"

    def brightness_set(self, level: int) -> str:
        try:
            import wmi
            level = max(0, min(100, int(level)))
            c = wmi.WMI(namespace="root\\wmi")
            methods = c.WmiMonitorBrightnessMethods()[0]
            methods.WmiSetBrightness(level, 0)
            return f"brightness set to {level}%"
        except ImportError:
            return "brightness needs wmi (pip install wmi)"
        except Exception as e:
            return f"failed: {e}"

    def brightness_get(self) -> str:
        try:
            import wmi
            c = wmi.WMI(namespace="root\\wmi")
            cur = c.WmiMonitorBrightness()[0].CurrentBrightness
            return f"brightness: {cur}%"
        except ImportError:
            return "brightness needs wmi (pip install wmi)"
        except Exception as e:
            return f"failed: {e}"

    def lock_screen(self) -> str:
        try:
            ctypes.windll.user32.LockWorkStation()
            return "screen locked"
        except Exception as e:
            return f"failed: {e}"

    def empty_recycle_bin(self) -> str:
        if not self.approve("Empty the recycle bin?", "This permanently deletes all items in the recycle bin."):
            return "denied"
        try:
            r = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
            return "recycle bin emptied" if r == 0 else f"failed: SHEmptyRecycleBinW returned {r}"
        except Exception as e:
            return f"failed: {e}"

    def set_wallpaper(self, path: str) -> str:
        try:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return f"file not found: {p}"
            SPI_SETDESKWALLPAPER = 20
            ok = ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, str(p), 3)
            return f"wallpaper set to {p.name}" if ok else "failed to set wallpaper"
        except Exception as e:
            return f"failed: {e}"


# ==============================================================================
# MediaTools
# ==============================================================================
class MediaTools:
    def play_pause(self) -> str:
        _tap_vk(VK["media_play_pause"])
        return "play/pause"
    def next_track(self) -> str:
        _tap_vk(VK["media_next"])
        return "next track"
    def prev_track(self) -> str:
        _tap_vk(VK["media_prev"])
        return "previous track"
    def stop(self) -> str:
        _tap_vk(VK["media_stop"])
        return "stop"


# ==============================================================================
# ClipboardTools
# ==============================================================================
class ClipboardTools:
    def __init__(self):
        self._cache = None

    def _api(self):
        if self._cache:
            return self._cache
        from ctypes import wintypes as wt
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32")
        u.OpenClipboard.argtypes, u.OpenClipboard.restype = [wt.HWND], wt.BOOL
        u.CloseClipboard.argtypes, u.CloseClipboard.restype = [], wt.BOOL
        u.EmptyClipboard.argtypes, u.EmptyClipboard.restype = [], wt.BOOL
        u.GetClipboardData.argtypes, u.GetClipboardData.restype = [wt.UINT], wt.HANDLE
        u.SetClipboardData.argtypes, u.SetClipboardData.restype = [wt.UINT, wt.HANDLE], wt.HANDLE
        k.GlobalAlloc.argtypes, k.GlobalAlloc.restype = [wt.UINT, ctypes.c_size_t], wt.HGLOBAL
        k.GlobalLock.argtypes, k.GlobalLock.restype = [wt.HGLOBAL], ctypes.c_void_p
        k.GlobalUnlock.argtypes, k.GlobalUnlock.restype = [wt.HGLOBAL], wt.BOOL
        self._cache = (u, k)
        return self._cache

    def _open(self, u):
        for _ in range(10):
            if u.OpenClipboard(None):
                return True
            time.sleep(0.02)
        return False

    def get(self) -> str:
        try:
            u, k = self._api()
            if not self._open(u):
                return "clipboard busy"
            try:
                h = u.GetClipboardData(13)
                if not h:
                    return "(clipboard empty)"
                ptr = k.GlobalLock(h)
                if not ptr:
                    return "(clipboard empty)"
                try:
                    text = ctypes.wstring_at(ptr)
                    return text[:5000] if text else "(clipboard empty)"
                finally:
                    k.GlobalUnlock(h)
            finally:
                u.CloseClipboard()
        except Exception as e:
            return f"failed: {e}"

    def set(self, text: str) -> str:
        try:
            u, k = self._api()
            data = (text or "").encode("utf-16-le") + b"\x00\x00"
            h = k.GlobalAlloc(0x0042, len(data))
            ptr = k.GlobalLock(h)
            ctypes.memmove(ptr, data, len(data))
            k.GlobalUnlock(h)
            if not self._open(u):
                return "clipboard busy"
            try:
                u.EmptyClipboard()
                u.SetClipboardData(13, h)
            finally:
                u.CloseClipboard()
            return f"clipboard set ({len(text)} chars)"
        except Exception as e:
            return f"failed: {e}"


# ==============================================================================
# NotifyTools
# ==============================================================================
class NotifyTools:
    def notify(self, title: str, message: str = "") -> str:
        try:
            title_b64 = base64.b64encode((title or "Karen").encode("utf-8")).decode("ascii")
            msg_b64 = base64.b64encode((message or "").encode("utf-8")).decode("ascii")
            script = (
                f'$title = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("{title_b64}"));'
                f'$msg = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("{msg_b64}"));'
                '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;'
                '$t = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;'
                '$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($t);'
                '$xml.GetElementsByTagName("text")[0].AppendChild($xml.CreateTextNode($title)) > $null;'
                '$xml.GetElementsByTagName("text")[1].AppendChild($xml.CreateTextNode($msg)) > $null;'
                '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Karen")'
                '.Show([Windows.UI.Notifications.ToastNotification]::new($xml))'
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, timeout=10, creationflags=NO_WINDOW)
            return "notified"
        except Exception as e:
            return f"failed: {e}"


# ==============================================================================
# OcrTools
# ==============================================================================
class OcrTools:
    def __init__(self):
        self._available = None

    def _check(self) -> bool:
        if self._available is None:
            try:
                import pytesseract  # noqa
                self._available = True
            except ImportError:
                self._available = False
        return self._available

    def screen(self, max_chars: int = 4000) -> str:
        if not self._check():
            return "ocr needs pytesseract (pip install pytesseract) + tesseract binary"
        try:
            import pytesseract
            img = _grab()
            text = pytesseract.image_to_string(img)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            return text[:max_chars] or "(no text found)"
        except Exception as e:
            return f"ocr failed: {e}"

    def window(self, window: str = "", max_chars: int = 4000) -> str:
        if not self._check():
            return "ocr needs pytesseract (pip install pytesseract) + tesseract binary"
        try:
            import pytesseract
            img = _grab()
            if window:
                wt = WinTools(lambda a, d: True)
                w = wt._window(window)
                if w is not None:
                    x, y, ww, hh = _rect(w)
                    if ww and hh:
                        img = _grab((x, y, ww, hh))
            text = pytesseract.image_to_string(img)
            return (re.sub(r"\n{3,}", "\n\n", text).strip() or "(no text found)")[:max_chars]
        except Exception as e:
            return f"ocr failed: {e}"

    def find_text(self, target: str) -> str:
        if not self._check():
            return "ocr needs pytesseract (pip install pytesseract) + tesseract binary"
        if not target:
            return "target required"
        try:
            import pytesseract
            img = _grab()
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            needle = target.lower()
            matches = []
            for i, word in enumerate(data['text']):
                if needle in (word or '').lower() and int(data['conf'][i]) > 40:
                    x = data['left'][i] + data['width'][i] // 2
                    y = data['top'][i] + data['height'][i] // 2
                    matches.append((x, y, word))
            if not matches:
                return f"'{target}' not found on screen"
            if len(matches) == 1:
                x, y, w = matches[0]
                return f"found '{w}' at ({x}, {y})"
            top = ", ".join(f"({x},{y}) '{w}'" for x, y, w in matches[:6])
            return f"found {len(matches)} matches: {top}"
        except Exception as e:
            return f"ocr failed: {e}"


# ==============================================================================
# ScheduleTools
# ==============================================================================
class ScheduleTools:
    def __init__(self, scheduler=None):
        self.scheduler = scheduler

    def _require(self):
        if self.scheduler is None:
            return "scheduled tasks aren't available in this mode"
        return None

    def add(self, description: str) -> str:
        err = self._require()
        if err:
            return err
        try:
            from tasks import TaskParser, describe_task
            task = TaskParser.parse(description)
            if not task:
                return (f"could not parse {description!r}. Try 'remind me to call mom in 20 minutes' "
                        "or 'at 5pm open spotify'.")
            self.scheduler.add(task)
            return f"scheduled: {describe_task(task)}"
        except Exception as e:
            return f"failed: {e}"

    def list(self) -> str:
        err = self._require()
        if err:
            return err
        try:
            from tasks import describe_task
            tasks = self.scheduler.list_tasks()
            if not tasks:
                return "no tasks scheduled"
            return "\n".join(f"[{t['id']}] {describe_task(t)}" for t in tasks)
        except Exception as e:
            return f"failed: {e}"

    def cancel(self, target: str) -> str:
        err = self._require()
        if err:
            return err
        try:
            s = str(target).strip().lower()
            if s in ("all", "everything"):
                n = self.scheduler.count
                self.scheduler.clear()
                return f"cleared {n} task(s)"
            for t in self.scheduler.list_tasks():
                tid = str(t.get("id", ""))
                txt = str(t.get("text", "")).lower()
                if tid.startswith(s) or s in txt:
                    self.scheduler.remove(tid)
                    return f"cancelled task {tid} ({t.get('text')})"
            return f"no task matching {target!r}"
        except Exception as e:
            return f"failed: {e}"


# ==============================================================================
# RegistryTools
# ==============================================================================
class RegistryTools:
    HIVES = {
        "HKLM": "HKEY_LOCAL_MACHINE",
        "HKCU": "HKEY_CURRENT_USER",
        "HKCR": "HKEY_CLASSES_ROOT",
        "HKU": "HKEY_USERS",
        "HKCC": "HKEY_CURRENT_CONFIG",
    }

    def get(self, key_path: str, value_name: str = "") -> str:
        try:
            import winreg
            if not key_path:
                return "key_path required"
            parts = key_path.replace("/", "\\").split("\\", 1)
            hive_key = parts[0].upper()
            hive_name = self.HIVES.get(hive_key)
            if not hive_name:
                return f"unknown hive {hive_key!r} (use HKLM/HKCU/HKCR/HKU/HKCC)"
            hive = getattr(winreg, hive_name)
            subkey = parts[1] if len(parts) > 1 else ""
            with winreg.OpenKey(hive, subkey) as k:
                v, t = winreg.QueryValueEx(k, value_name)
                return f"{value_name or '(default)'} = {v!r} (type {t})"
        except FileNotFoundError:
            return f"not found: {key_path}\\{value_name}"
        except Exception as e:
            return f"failed: {e}"


# ==============================================================================
# AppTools
# ==============================================================================
class AppTools:
    def __init__(self, app_index=None, approve=None):
        self.app_index = app_index
        self.approve = approve or (lambda a, d: False)

    def list(self, query: str = "", limit: int = 20) -> str:
        if self.app_index is None:
            return "app index not available"
        try:
            apps = self.app_index.list_all(query, limit)
            if not apps:
                return f"no apps matching {query!r}" if query else "no apps indexed"
            return "\n".join(f"{a['name']}  [{a['kind']}]  {a['exe'][:50]}" for a in apps)
        except Exception as e:
            return f"failed: {e}"

    def running(self, name: str) -> str:
        try:
            import psutil
            s = (name or "").lower()
            found = []
            for p in psutil.process_iter(['pid', 'name']):
                try:
                    n = (p.info['name'] or '').lower()
                    if s in n or n.replace(".exe", "") in s:
                        found.append(f"{p.info['name']} (pid {p.pid})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return ("running:\n" + "\n".join(found)) if found else f"{name!r} is not running"
        except Exception as e:
            return f"failed: {e}"

    def close(self, name: str) -> str:
        try:
            import psutil
            s = (name or "").lower()
            killed = []
            for p in psutil.process_iter(['pid', 'name']):
                try:
                    n = (p.info['name'] or '').lower()
                    if s in n or n.replace(".exe", "") in s:
                        if self.approve(f"Close {p.info['name']} (pid {p.pid})?", ""):
                            p.terminate()
                            killed.append(p.info['name'])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return f"closed {len(killed)} process(es)" if killed else f"no running process matching {name!r}"
        except Exception as e:
            return f"failed: {e}"


# ==============================================================================
# ToolKit — the aggregator
# ==============================================================================
class ToolKit:
    def __init__(self, approve: Callable[[str, str], bool],
                 allowed_folders: Optional[List[str]] = None,
                 on_event: Optional[Callable[[str], None]] = None,
                 task_scheduler=None,
                 app_index=None):
        self.approve = approve
        self.on_event = on_event or (lambda _m: None)
        self.win = WinTools(approve)
        self.files = FileTools(approve, allowed_folders)
        self.shell = ShellTools(approve)
        self.web = WebTools()
        self.sys = SystemTools(approve)
        self.media = MediaTools()
        self.clip = ClipboardTools()
        self.notif = NotifyTools()
        self.ocr = OcrTools()
        self.sched = ScheduleTools(task_scheduler)
        self.apps = AppTools(app_index, approve)
        self.registry = RegistryTools()
        self.app_index = app_index
        self.task_scheduler = task_scheduler
        self._finished = False
        self.summary = ""

    @staticmethod
    def schemas() -> List[dict]:
        def t(name, desc, props=None, req=()):
            return {"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props or {},
                               "required": list(req)}}}
        S = lambda d="": {"type": "string", "description": d}
        I = lambda d="": {"type": "integer", "description": d}
        B = lambda d="": {"type": "boolean", "description": d}
        N = lambda d="": {"type": "number", "description": d}
        return [
            # ---- windows / UI ------------------------------------------------
            t("list_windows", "List visible top-level windows.",
              {"limit": I("max rows, default 30")}),
            t("dump_ui_tree", "Dump the UI Automation tree as numbered nodes. Call BEFORE clicking.",
              {"window": S("title substring; empty=foreground"), "max_depth": I("default 6"),
               "max_nodes": I("default 220")}),
            t("find_element", "Check that a control exists.",
              {"window": S(), "name": S(), "automation_id": S(),
               "control_type": S("Button, Edit, MenuItem…"), "class_name": S()}),
            t("wait_for_element", "Wait for a control to appear (up to N seconds).",
              {"window": S(), "name": S(), "automation_id": S(), "control_type": S(),
               "timeout": N("seconds, default 5")}),
            t("wait_for_window", "Wait for a window to appear (up to N seconds).",
              {"title": S(), "timeout": N("seconds, default 5")}, ("title",)),
            t("read_text", "Return all text exposed by the active window.",
              {"window": S(), "max_chars": I()}),
            t("screenshot", "Save a full-screen PNG; returns the path.", {"window": S(), "path": S()}),
            t("screenshot_region", "Capture a rectangle of the screen.",
              {"x": I(), "y": I(), "w": I(), "h": I(), "path": S()}, ("x", "y", "w", "h")),
            t("wait", "Pause for UI to render.", {"seconds": N()}),
            t("focus_window", "Bring a window to the foreground.", {"title": S()}, ("title",)),
            t("window_op", "focus | minimize | maximize | restore | close.",
              {"title": S(), "op": S()}, ("title", "op")),
            t("window_snap", "Snap a window to left/right/up (maximize)/down.",
              {"title": S(), "position": S("left|right|up|down")}, ("title", "position")),
            t("launch_app", "Open an app by name.", {"name": S(), "wait_s": N()}, ("name",)),
            t("list_apps", "List or search installed apps via the local app index.",
              {"query": S("substring; empty lists everything"), "limit": I()}),
            t("app_running", "Check if a process/app is running.", {"name": S()}, ("name",)),
            t("close_app", "Kill a running app by name.", {"name": S()}, ("name",)),
            t("click_element", "Invoke a control via UI Automation by #index or identifier. "
                               "There is no mouse; only the element's own pattern is used.",
              {"index": I(), "window": S(), "name": S(), "automation_id": S(),
               "control_type": S(), "verify_name": S(), "verify_timeout": N()}),
            t("type_into", "Type text into a control.",
              {"text": S(), "index": I(), "window": S(), "name": S(),
               "automation_id": S(), "submit": B()}, ("text",)),
            t("type_text", "Type text into whatever control currently has focus. "
                           "No element lookup — use this for 'type hello' without a field. "
                           "Prefer this over type_into when the user hasn't named a specific control.",
              {"text": S("the text to type"), "submit": B("press Enter after, default false")},
              ("text",)),
            t("press_keys", "Send a key chord (e.g. 'ctrl+s').", {"keys": S()}, ("keys",)),

            # ---- files -------------------------------------------------------
            t("list_dir", "List a folder.", {"path": S(), "pattern": S()}),
            t("read_file", "Read a text file with line numbers.",
              {"path": S(), "offset": I(), "limit": I()}, ("path",)),
            t("write_file", "Create or overwrite a text file.",
              {"path": S(), "content": S()}, ("path", "content")),
            t("append_file", "Append text to a file.",
              {"path": S(), "content": S()}, ("path", "content")),
            t("edit_file", "Replace a string inside a file.",
              {"path": S(), "find": S(), "replace": S(), "count": I()},
              ("path", "find", "replace")),
            t("move_file", "Move or rename.", {"src": S(), "dst": S()}, ("src", "dst")),
            t("copy_file", "Copy a file or folder.", {"src": S(), "dst": S()}, ("src", "dst")),
            t("create_folder", "Create a directory (and parents).", {"path": S()}, ("path",)),
            t("delete_file", "Delete a file or folder (approval required).",
              {"path": S()}, ("path",)),
            t("file_info", "Show size, modified time, and type of a path.", {"path": S()}, ("path",)),
            t("find_files", "Find files by name/pattern under a folder.",
              {"pattern": S("e.g. *.pdf"), "path": S(), "limit": I()}, ("pattern",)),
            t("search_files", "Search file contents.", {"pattern": S(), "path": S(), "glob": S()}, ("pattern",)),

            # ---- shell -------------------------------------------------------
            t("run_shell", "Run a cmd.exe command.", {"command": S(), "cwd": S(), "timeout": I()}, ("command",)),
            t("run_powershell", "Run a PowerShell command.", {"command": S(), "timeout": I()}, ("command",)),

            # ---- web ---------------------------------------------------------
            t("fetch_url", "Fetch a URL and return cleaned text.", {"url": S(), "max_chars": I()}, ("url",)),
            t("open_url", "Open a URL in the default browser.", {"url": S()}, ("url",)),
            t("open_site", "Open a website by URL, domain or well-known name (youtube, gmail, github...) "
                           "in the default browser. Use this for websites, not launch_app.",
              {"target": S("e.g. youtube.com, https://..., gmail")}, ("target",)),
            t("web_search", "Search the web (Google by default) and open the results in the browser.",
              {"query": S(), "engine": S("google|images|youtube|maps|bing|duckduckgo|wikipedia")},
              ("query",)),

            # ---- system ------------------------------------------------------
            t("system_stats", "CPU, RAM, disk, and battery."),
            t("battery", "Battery percentage and charging state."),
            t("disk_usage", "Disk usage for a drive or path.", {"path": S("default C:")}),
            t("list_processes", "List top processes by CPU or memory.",
              {"sort_by": S("cpu|mem"), "limit": I()}),
            t("kill_process", "Kill a process by pid or name.", {"pid_or_name": S()}, ("pid_or_name",)),
            t("volume_get", "Current master volume and mute state."),
            t("volume_set", "Set master volume 0-100.", {"level": I()}, ("level",)),
            t("volume_mute", "Toggle mute."),
            t("brightness_get", "Current screen brightness."),
            t("brightness_set", "Set screen brightness 0-100.", {"level": I()}, ("level",)),
            t("lock_screen", "Lock the Windows session."),
            t("empty_recycle_bin", "Empty the recycle bin (asks for approval)."),
            t("set_wallpaper", "Set the desktop wallpaper from an image file.", {"path": S()}, ("path",)),

            # ---- media -------------------------------------------------------
            t("media_play_pause", "Toggle play/pause for the current media."),
            t("media_next", "Skip to the next track."),
            t("media_prev", "Go to the previous track."),
            t("media_stop", "Stop media playback."),

            # ---- clipboard / notify ------------------------------------------
            t("clipboard_get", "Read the current clipboard text."),
            t("clipboard_set", "Write text to the clipboard.", {"text": S()}, ("text",)),
            t("notify", "Show a Windows toast notification.",
              {"title": S(), "message": S()}, ("title",)),

            # ---- OCR ---------------------------------------------------------
            t("ocr_screen", "Read all text from the current screen (OCR).", {"max_chars": I()}),
            t("ocr_window", "OCR a specific window's content.",
              {"window": S("title substring; empty=foreground"), "max_chars": I()}),
            t("find_on_screen", "Locate text on the screen using OCR; returns its coordinates.",
              {"text": S()}, ("text",)),

            # ---- scheduling --------------------------------------------------
            t("schedule_task", "Schedule a reminder or action. Natural language. "
                               "Examples: 'remind me to call mom in 20 minutes', "
                               "'at 5pm open spotify', 'every day at 9am take a screenshot'.",
              {"description": S("full natural language sentence")}, ("description",)),
            t("list_scheduled_tasks", "List all scheduled tasks."),
            t("cancel_scheduled_task", "Cancel a scheduled task by id, or 'all'.",
              {"target": S("task id or 'all'")}, ("target",)),

            # ---- misc --------------------------------------------------------
            t("registry_get", "Read a registry value.",
              {"key_path": S("e.g. HKLM\\\\SOFTWARE\\\\Microsoft\\\\Windows\\\\CurrentVersion"),
               "value_name": S("empty for default")}, ("key_path",)),
            t("calculate", "Evaluate a math expression safely.", {"expression": S()}, ("expression",)),
            t("reply_message", "Type a reply into the message box of a chat app/window and send it "
                               "(send=false only types a draft). Use read_text first to see the conversation.",
              {"text": S("the message"), "app": S("window title, e.g. WhatsApp"),
               "contact": S("optional conversation to open first"), "send": B("default true")},
              ("text",)),
            t("ask_user", "Ask the user for approval.", {"question": S()}, ("question",)),
            t("finish", "Call when the goal is achieved or impossible.",
              {"summary": S(), "success": B()}, ("summary",)),
        ]

    @classmethod
    def short_schema(cls) -> str:
        lines = []
        for s in cls.schemas():
            fn = s["function"]
            props = fn["parameters"].get("properties", {})
            req = fn["parameters"].get("required", [])
            parts = []
            for k in req:
                ty = props.get(k, {}).get("type", "any")
                parts.append(f'"{k}": <{ty}>')
            args = ", ".join(parts) or ""
            lines.append(f'  {{"tool":"{fn["name"]}",{args}}}  — {fn["description"][:80]}')
        return "\n".join(lines)

    def call(self, name: str, args: dict) -> str:
        self.on_event(f"{name} {json.dumps(args, ensure_ascii=False)[:90]}")
        try:
            fn = getattr(self, f"_t_{name}", None)
            if fn is None:
                return f"unknown tool: {name}"
            return str(fn(**args))
        except TypeError as e:
            return f"bad arguments for {name}: {e}"
        except Exception as e:
            return f"{name} failed: {type(e).__name__}: {e}"

    # ---- window / UI delegates ----------------------------------------
    def _t_list_windows(self, limit=30): return self.win.list_windows(limit)
    def _t_dump_ui_tree(self, window="", max_depth=6, max_nodes=220):
        return self.win.dump_ui_tree(window, max_depth, max_nodes)
    def _t_find_element(self, window="", name="", automation_id="", control_type="", class_name=""):
        return self.win.find_element(window, name, automation_id, control_type, class_name)
    def _t_wait_for_element(self, window="", name="", automation_id="", control_type="", timeout=5.0):
        return self.win.wait_for_element(window, name, automation_id, control_type, timeout)
    def _t_wait_for_window(self, title, timeout=5.0):
        return self.win.wait_for_window(title, timeout)
    def _t_read_text(self, window="", max_chars=4000): return self.win.read_text(window, max_chars)
    def _t_screenshot(self, window="", path=""): return self.win.screenshot(window, path)
    def _t_screenshot_region(self, x, y, w, h, path=""): return self.win.screenshot_region(x, y, w, h, path)
    def _t_wait(self, seconds=1.0): return self.win.wait(seconds)
    def _t_focus_window(self, title): return self.win.focus_window(title)
    def _t_window_op(self, title, op): return self.win.window_op(title, op)
    def _t_window_snap(self, title, position): return self.win.snap(title, position)
    def _t_launch_app(self, name, wait_s=8.0):
        if self.app_index is not None:
            return self.app_index.launch(name, fallback=lambda n: self.win.launch_app(n, wait_s))
        return self.win.launch_app(name, wait_s)
    def _t_list_apps(self, query="", limit=20): return self.apps.list(query, limit)
    def _t_app_running(self, name): return self.apps.running(name)
    def _t_close_app(self, name): return self.apps.close(name)
    def _t_click_element(self, index=0, window="", name="", automation_id="",
                         control_type="", verify_name="", verify_timeout=0.0):
        return self.win.click_element(index, window, name, automation_id,
                                      control_type, verify_name, verify_timeout)
    def _t_type_into(self, text, index=0, window="", name="", automation_id="", submit=False):
        return self.win.type_into(text, index, window, name, automation_id, submit)
    def _t_type_text(self, text, submit=False):
        return self.win.type_text(text, submit)
    def _t_press_keys(self, keys): return send_chord(keys)

    # ---- files --------------------------------------------------------
    def _t_list_dir(self, path="~", pattern="*"): return self.files.list_dir(path, pattern)
    def _t_read_file(self, path, offset=0, limit=400): return self.files.read_file(path, offset, limit)
    def _t_write_file(self, path, content): return self.files.write_file(path, content)
    def _t_append_file(self, path, content): return self.files.append_file(path, content)
    def _t_edit_file(self, path, find, replace, count=0):
        return self.files.edit_file(path, find, replace, count)
    def _t_move_file(self, src, dst): return self.files.move(src, dst)
    def _t_copy_file(self, src, dst): return self.files.copy(src, dst)
    def _t_create_folder(self, path): return self.files.mkdir(path)
    def _t_delete_file(self, path): return self.files.delete(path)
    def _t_file_info(self, path): return self.files.info(path)
    def _t_find_files(self, pattern, path="~", limit=50):
        return self.files.find_by_name(pattern, path, limit)
    def _t_search_files(self, pattern, path="~", glob="*"): return self.files.search(pattern, path, glob)

    # ---- shell --------------------------------------------------------
    def _t_run_shell(self, command, cwd="", timeout=60): return self.shell.run(command, cwd, timeout)
    def _t_run_powershell(self, command, timeout=60): return self.shell.run_ps(command, timeout)

    # ---- web ----------------------------------------------------------
    def _t_fetch_url(self, url, max_chars=6000): return self.web.fetch(url, max_chars)
    def _t_open_url(self, url): return self.web.open_url(url)
    def _t_open_site(self, target): return self.web.open_site(target)
    def _t_web_search(self, query, engine="google"): return self.web.search(query, engine)

    # ---- system -------------------------------------------------------
    def _t_system_stats(self): return self.sys.stats()
    def _t_battery(self): return self.sys.battery()
    def _t_disk_usage(self, path="C:"): return self.sys.disk_usage(path)
    def _t_list_processes(self, sort_by="cpu", limit=20): return self.sys.list_processes(sort_by, limit)
    def _t_kill_process(self, pid_or_name): return self.sys.kill_process(pid_or_name)
    def _t_volume_get(self): return self.sys.volume_get()
    def _t_volume_set(self, level): return self.sys.volume_set(level)
    def _t_volume_mute(self): return self.sys.volume_mute()
    def _t_brightness_get(self): return self.sys.brightness_get()
    def _t_brightness_set(self, level): return self.sys.brightness_set(level)
    def _t_lock_screen(self): return self.sys.lock_screen()
    def _t_empty_recycle_bin(self): return self.sys.empty_recycle_bin()
    def _t_set_wallpaper(self, path): return self.sys.set_wallpaper(path)

    # ---- media --------------------------------------------------------
    def _t_media_play_pause(self): return self.media.play_pause()
    def _t_media_next(self): return self.media.next_track()
    def _t_media_prev(self): return self.media.prev_track()
    def _t_media_stop(self): return self.media.stop()

    # ---- clipboard / notify ------------------------------------------
    def _t_clipboard_get(self): return self.clip.get()
    def _t_clipboard_set(self, text): return self.clip.set(text)
    def _t_notify(self, title, message=""): return self.notif.notify(title, message)

    # ---- OCR ----------------------------------------------------------
    def _t_ocr_screen(self, max_chars=4000): return self.ocr.screen(max_chars)
    def _t_ocr_window(self, window="", max_chars=4000): return self.ocr.window(window, max_chars)
    def _t_find_on_screen(self, text): return self.ocr.find_text(text)

    # ---- scheduling ---------------------------------------------------
    def _t_schedule_task(self, description): return self.sched.add(description)
    def _t_list_scheduled_tasks(self): return self.sched.list()
    def _t_cancel_scheduled_task(self, target): return self.sched.cancel(target)

    # ---- misc ---------------------------------------------------------
    def _t_registry_get(self, key_path, value_name=""): return self.registry.get(key_path, value_name)
    def _t_calculate(self, expression):
        try:
            allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
            allowed.update({"abs": abs, "min": min, "max": max, "round": round, "sum": sum,
                            "int": int, "float": float, "pow": pow})
            result = eval(expression, {"__builtins__": {}}, allowed)
            return f"{expression} = {result}"
        except Exception as e:
            return f"calc error: {e}"
    def _t_reply_message(self, text, app="", contact="", send=True):
        return self.win.reply_message(text, app, contact, send)
    def _t_ask_user(self, question):
        return "approved" if self.approve(question, "user question") else "denied"
    def _t_finish(self, summary="", success=True):
        self._finished = True
        self.summary = summary
        return summary or "done"