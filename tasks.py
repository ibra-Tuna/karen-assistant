# ==============================================================================
# tasks.py — scheduled task system.
#
# Users add tasks by talking:
#   "remind me to call mom in 20 minutes"
#   "in 5 minutes open spotify"
#   "at 9am tomorrow open email"
#   "every day at 5pm take a screenshot"
#
# The scheduler runs in a background thread and pushes
# ("task_due", task) into owner.ui_queue. All real work
# (dialog, execution) happens on the UI thread.
# ==============================================================================
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Dict

TaskDict = Dict[str, object]


class TaskParser:
    """Turn natural language into task dicts. No side effects."""

    @staticmethod
    def _to_seconds(n: int, unit: str) -> int:
        u = (unit or "minute").lower().rstrip("s")
        return n * {"second": 1, "minute": 60, "hour": 3600, "day": 86400}.get(u, 60)

    @staticmethod
    def _parse_time(h: str, m: Optional[str], ampm: Optional[str]):
        try:
            hour = int(h)
            minute = int(m) if m else 0
        except (TypeError, ValueError):
            return None
        ap = (ampm or "").replace(".", "").lower()
        if ap == "pm" and hour < 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
        if not (0 <= hour < 24 and 0 <= minute < 60):
            return None
        return hour, minute

    @staticmethod
    def _next_at(hour: int, minute: int, now: float) -> float:
        base = datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if base.timestamp() <= now:
            base += timedelta(days=1)
        return base.timestamp()

    @staticmethod
    def _make(text: str, when: float, kind: str, repeat: str) -> TaskDict:
        return {
            "id": uuid.uuid4().hex[:8],
            "text": text.strip(),
            "when": float(when),
            "kind": kind,
            "repeat": repeat,
            "created": time.time(),
        }

    @classmethod
    def parse(cls, text: str, now: Optional[float] = None) -> Optional[TaskDict]:
        if not text:
            return None
        now = now if now is not None else time.time()
        t = text.strip()

        # remind me to X in N unit[s]
        m = re.match(
            r"^(?:please\s+)?remind\s+me(?:\s+to)?\s+(.+?)\s+in\s+(\d+)\s*(seconds?|minutes?|hours?|days?)\s*[.!?]*$",
            t, re.I)
        if m:
            return cls._make(m.group(1), now + cls._to_seconds(int(m.group(2)), m.group(3)),
                             "reminder", "none")

        # remind me to X at TIME
        m = re.match(
            r"^(?:please\s+)?remind\s+me(?:\s+to)?\s+(.+?)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s*[.!?]*$",
            t, re.I)
        if m:
            hm = cls._parse_time(m.group(2), m.group(3), m.group(4))
            if hm:
                return cls._make(m.group(1), cls._next_at(hm[0], hm[1], now), "reminder", "none")

        # in N unit[s] ACTION
        m = re.match(
            r"^(?:please\s+)?in\s+(\d+)\s*(seconds?|minutes?|hours?|days?)\s+(?:please\s+)?(.+?)\s*[.!?]*$",
            t, re.I)
        if m:
            return cls._make(m.group(3), now + cls._to_seconds(int(m.group(1)), m.group(2)),
                             "agent", "none")

        # at TIME tomorrow ACTION / tomorrow at TIME ACTION
        for rx in (
            r"^(?:please\s+)?at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s+tomorrow\s+(?:please\s+)?(.+?)\s*[.!?]*$",
            r"^(?:please\s+)?tomorrow\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s+(?:please\s+)?(.+?)\s*[.!?]*$",
        ):
            m = re.match(rx, t, re.I)
            if m:
                hm = cls._parse_time(m.group(1), m.group(2), m.group(3))
                if hm:
                    tomorrow = datetime.fromtimestamp(now).replace(
                        hour=hm[0], minute=hm[1], second=0, microsecond=0)
                    if tomorrow.timestamp() <= now:
                        tomorrow += timedelta(days=1)
                    return cls._make(m.group(4), tomorrow.timestamp(), "agent", "none")

        # every day at TIME ACTION
        m = re.match(
            r"^(?:please\s+)?(?:every\s+day|daily)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s+(?:please\s+)?(.+?)\s*[.!?]*$",
            t, re.I)
        if m:
            hm = cls._parse_time(m.group(1), m.group(2), m.group(3))
            if hm:
                return cls._make(m.group(4), cls._next_at(hm[0], hm[1], now), "agent", "daily")

        # every hour ACTION
        m = re.match(r"^(?:please\s+)?(?:every\s+hour|hourly)\s+(?:please\s+)?(.+?)\s*[.!?]*$", t, re.I)
        if m:
            return cls._make(m.group(1), now + 3600, "agent", "hourly")

        # at TIME ACTION
        m = re.match(
            r"^(?:please\s+)?at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s+(?:please\s+)?(.+?)\s*[.!?]*$",
            t, re.I)
        if m:
            hm = cls._parse_time(m.group(1), m.group(2), m.group(3))
            if hm:
                return cls._make(m.group(4), cls._next_at(hm[0], hm[1], now), "agent", "none")

        return None


def describe_task(task: TaskDict) -> str:
    when = datetime.fromtimestamp(float(task["when"]))
    delta = float(task["when"]) - time.time()
    if delta < 0:
        rel = "overdue"
    elif delta < 60:
        rel = f"in {int(delta)}s"
    elif delta < 3600:
        rel = f"in {int(delta / 60)}m"
    elif delta < 86400:
        rel = f"in {int(delta / 3600)}h"
    else:
        rel = f"in {int(delta / 86400)}d"
    repeat = task.get("repeat", "none")
    rep_str = "" if repeat == "none" else f" · {repeat}"
    icon = "⏰" if task.get("kind") == "reminder" else "🤖"
    return f"{icon} {when.strftime('%H:%M on %d %b')} ({rel}){rep_str}  ·  {task['text']}"


class TaskScheduler:
    """Persistent scheduled tasks. Fires into owner.ui_queue."""

    TICK = 5.0

    def __init__(self, owner, path: str):
        self.owner = owner
        self.path = path
        self._lock = threading.Lock()
        self._tasks: List[TaskDict] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._load()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._tasks)

    def add(self, task: TaskDict) -> TaskDict:
        with self._lock:
            self._tasks.append(task)
            self._save_locked()
        return task

    def remove(self, tid: str) -> bool:
        with self._lock:
            before = len(self._tasks)
            self._tasks = [x for x in self._tasks if x.get("id") != tid]
            changed = len(self._tasks) != before
            if changed:
                self._save_locked()
            return changed

    def clear(self):
        with self._lock:
            self._tasks = []
            self._save_locked()

    def list_tasks(self) -> List[TaskDict]:
        with self._lock:
            return sorted(self._tasks, key=lambda x: float(x.get("when", 0)))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="task-scheduler")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop.wait(self.TICK)

    def _tick(self):
        now = time.time()
        due: List[TaskDict] = []
        with self._lock:
            still: List[TaskDict] = []
            changed = False
            for t in self._tasks:
                if float(t.get("when", 0)) <= now:
                    due.append(t)
                    rep = t.get("repeat", "none")
                    if rep in ("hourly", "daily", "weekly"):
                        step = {"hourly": 3600, "daily": 86400, "weekly": 604800}[rep]
                        t["when"] = float(t["when"]) + step
                        still.append(t)
                    changed = True
                else:
                    still.append(t)
            if changed:
                self._tasks = still
                self._save_locked()
        for t in due:
            try:
                self.owner.ui_queue.put(("task_due", dict(t)))
            except Exception:
                pass

    def _load(self):
        try:
            if not os.path.isfile(self.path):
                return
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("tasks"), list):
                self._tasks = [x for x in data["tasks"]
                               if isinstance(x, dict) and "when" in x and "text" in x]
        except Exception:
            self._tasks = []

    def _save_locked(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"tasks": self._tasks}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            pass