"""Serialized, restart-aware capacity checks; callbacks own account and task work."""

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class Automation:
    def __init__(self, *, state_file, settings, synchronize, snapshot, active,
                 start_task, config_lock, clock=None):
        self.state_file = state_file
        self.settings = settings
        self.synchronize = synchronize
        self.snapshot = snapshot
        self.active = active
        self.start_task = start_task
        self.config_lock = config_lock
        self.clock = clock or time.time
        self.run_lock = threading.Lock()
        self.state_lock = threading.RLock()

    def _path(self):
        return Path(self.state_file() if callable(self.state_file) else self.state_file)

    def _read(self):
        try:
            data = json.loads(self._path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _update(self, **values):
        with self.state_lock:
            data = self._read()
            data.update(values)
            path = self._path()
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def status(self):
        with self.state_lock:
            data = self._read()
        enabled = self.settings()["auto_register"]
        result = {
            "running": self.run_lock.locked(),
            "outcome": data.get("outcome", "idle" if enabled else "disabled"),
            "message": data.get("message", "等待下次检查" if enabled else "自动补号已关闭"),
            "registered_count": data.get("registered_count", 0),
            "synced": data.get("synced", 0), "failed": data.get("failed", 0),
        }
        for field in ("last_check_at", "next_check_at"):
            stamp = data.get(field)
            try:
                result[field] = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp else None
            except (TypeError, ValueError, OverflowError, OSError):
                result[field] = None
        if not enabled:
            result["next_check_at"] = None
        if result["outcome"] == "running" and not result["running"]:
            result.update(outcome="error", message="上次检查中断，等待下一轮检查")
        return result

    def configure(self):
        with self.config_lock:
            settings = self.settings()
            due = self.clock() + settings["loop_seconds"] if settings["auto_register"] else None
            self._update(next_check_at=due)

    def poll(self):
        if not self.settings()["auto_register"]:
            return None
        due = self._read().get("next_check_at")
        if not isinstance(due, (int, float)):
            self.configure()
            return None
        if self.clock() < due:
            return None
        try:
            return self.check()
        except RuntimeError:
            return None  # An explicit check is already running.

    def check(self, allow_registration=True):
        if not self.run_lock.acquire(blocking=False):
            raise RuntimeError("已有容量检查正在运行")
        return self._run_locked(allow_registration)

    def start(self):
        if not self.run_lock.acquire(blocking=False):
            raise RuntimeError("已有容量检查正在运行")
        try:
            threading.Thread(target=self._run_locked, args=(True,),
                             name="nodes-capacity-check", daemon=True).start()
        except Exception:
            self.run_lock.release()
            raise

    def _finish(self, outcome, message, *, synced=0, failed=0, registered_count=0):
        with self.config_lock:
            settings = self.settings()
            now = self.clock()
            self._update(outcome=outcome, message=message, last_check_at=now,
                         next_check_at=now + settings["loop_seconds"] if settings["auto_register"] else None,
                         synced=synced, failed=failed, registered_count=registered_count)

    def _run_locked(self, allow_registration):
        capacity, task = None, None
        synced = failed = 0
        try:
            # Persist a future deadline before network I/O so a restart cannot
            # immediately repeat an unfinished check or registration decision.
            self.configure()
            self._update(outcome="running", message="正在同步账号并检查容量", registered_count=0)
            if self.active():
                self._finish("busy", "已有注册任务，稍后再检查")
            else:
                result = self.synchronize()
                synced, failed = len(result["synced"]), len(result["failed"])
                with self.config_lock:
                    settings = self.settings()
                    capacity = self.snapshot()
                    if result.get("blocking_failed", failed):
                        self._finish("sync_failed", "部分有效账号同步失败，本轮暂停补号", synced=synced, failed=failed)
                    elif capacity["unknown_bandwidth_accounts"]:
                        self._finish("unknown_capacity", "部分账号流量未知，本轮暂停补号", synced=synced, failed=failed)
                    elif not settings["auto_register"] or not allow_registration:
                        self._finish("disabled", "账号已同步，自动补号已关闭", synced=synced, failed=failed)
                    elif capacity["needed_accounts"] <= 0:
                        self._finish("satisfied", "容量已达标，无需补号", synced=synced, failed=failed)
                    elif self.active():
                        self._finish("busy", "已有注册任务，稍后再检查", synced=synced, failed=failed)
                    else:
                        count = min(settings["max_register_per_round"], capacity["needed_accounts"])
                        task = self.start_task(count, 1)
                        self._finish("registration_started", f"容量不足，已启动 {count} 个账号的注册任务",
                                     synced=synced, failed=failed, registered_count=count)
        except RuntimeError:
            self._finish("busy", "已有账号操作正在运行，稍后重试", synced=synced, failed=failed)
        except Exception:
            self._finish("error", "本轮检查失败，保留现有数据并等待下次检查", synced=synced, failed=failed)
        finally:
            self.run_lock.release()
        return {"capacity": capacity, "task": task}
