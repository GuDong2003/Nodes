# -*- coding: utf-8 -*-
"""Private inventory/history/audit storage, adapted from upstream f140ea8."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_lock = threading.RLock()
INVENTORY_FILE = 'inventory.json'
INVENTORY_HISTORY_FILE = 'inventory_history.jsonl'
AUDIT_FILE = 'audit.jsonl'
HISTORY_MAX_LINES = 720
AUDIT_MAX_LINES = 2000
_SECRET_KEYS = re.compile(r'(password|passwd|secret|token|authorization|credential|cookie)', re.I)
_USERINFO = re.compile(r'(?:(?<=://)|(?<=\s)|^)[^\s/@]+@(?=[^\s/]+)')


def redact(value):
    """Remove credential fields and URL userinfo before persisting public state."""
    if isinstance(value, dict):
        return {str(key): '[redacted]' if _SECRET_KEYS.search(str(key)) else redact(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _USERINFO.sub('', value)
    return value


def _atomic_write(path, text):
    """Replace a file atomically using a private, uniquely named sibling."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _path(data_dir, name):
    return Path(data_dir) / name if data_dir else None


def _read_json(path):
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _read_lines(path):
    if path is None:
        return []
    try:
        return path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return []


def _append_jsonl(path, entry, keep):
    lines = _read_lines(path)
    lines.append(json.dumps(redact(entry), ensure_ascii=False, allow_nan=False))
    _atomic_write(path, '\n'.join(lines[-keep:]) + '\n')


def _limit(value, default):
    try:
        return max(1, min(500, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def save_inventory_snapshot(data_dir, inventory, min_interval_sec=300):
    """Write latest and append history when changed or the interval has elapsed."""
    latest = _path(data_dir, INVENTORY_FILE)
    history = _path(data_dir, INVENTORY_HISTORY_FILE)
    if latest is None:
        return None
    payload = redact(dict(inventory or {}))
    payload.update(recorded_at=datetime.now(timezone.utc).isoformat(), recorded_unix=int(time.time()))
    point = {key: payload.get(key, '' if key == 'profile_id' else 0)
             for key in ('recorded_at', 'recorded_unix', 'scanned', 'accepted', 'rejected',
                         'skipped_unprobed', 'version', 'profile_id')}
    with _lock:
        _atomic_write(latest, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
        previous = load_inventory_history(data_dir, limit=1)
        last = previous[-1] if previous else {}
        fields = ('scanned', 'accepted', 'rejected', 'skipped_unprobed', 'version', 'profile_id')
        try:
            recent = point['recorded_unix'] - int(last.get('recorded_unix') or 0) < max(0, float(min_interval_sec))
        except (TypeError, ValueError, OverflowError):
            recent = False
        if not recent or any(last.get(key) != point[key] for key in fields):
            _append_jsonl(history, point, HISTORY_MAX_LINES)
    return payload


def load_inventory_latest(data_dir):
    payload = _read_json(_path(data_dir, INVENTORY_FILE))
    return redact(payload) if isinstance(payload, dict) else None


def load_inventory_history(data_dir, limit=48):
    rows = []
    for line in reversed(_read_lines(_path(data_dir, INVENTORY_HISTORY_FILE))):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(redact(item))
            if len(rows) >= _limit(limit, 48):
                break
    return list(reversed(rows))


def append_audit(data_dir, kind, action, detail=None, actor='web'):
    path = _path(data_dir, AUDIT_FILE)
    if path is None:
        return None
    entry = redact({'at': datetime.now(timezone.utc).isoformat(), 'unix': int(time.time()),
                    'kind': str(kind or 'system'), 'action': str(action or ''),
                    'actor': str(actor or 'web'),
                    'detail': detail if isinstance(detail, dict) else {'message': str(detail or '')}})
    with _lock:
        _append_jsonl(path, entry, AUDIT_MAX_LINES)
    return entry


def load_audit(data_dir, kind=None, limit=50):
    wanted = str(kind or '').strip().lower()
    rows = []
    for line in reversed(_read_lines(_path(data_dir, AUDIT_FILE))):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict) or wanted and str(item.get('kind') or '').lower() != wanted:
            continue
        rows.append(redact(item))
        if len(rows) >= _limit(limit, 50):
            break
    return rows
