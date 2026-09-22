# -*- coding: utf-8 -*-
"""Authenticated web dashboard for the Nodes registration worker."""

import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash
import requests

import pool
import proxyscrape_register as worker


BASE_DIR = Path(__file__).resolve().parent
ACCOUNT_DIR = Path(os.environ.get("NODES_ACCOUNT_DIR", BASE_DIR / "account"))
NODE_DIR = Path(os.environ.get("NODES_NODE_DIR", BASE_DIR / "node"))
WEB_DATA_DIR = Path(os.environ.get("NODES_WEB_DATA_DIR", BASE_DIR / "web-data"))
CONFIG_FILE = Path(os.environ.get("NODES_CONFIG_FILE", BASE_DIR / "config.local.json"))
TASKS_FILE = WEB_DATA_DIR / "tasks.json"

ACCOUNT_DIR.mkdir(parents=True, exist_ok=True)
NODE_DIR.mkdir(parents=True, exist_ok=True)
WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_config():
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


SETTINGS_KEYS = (
    "mail_provider",
    "mail_api_base",
    "mail_api_key",
    "mail_type",
    "mail_suffix",
    "mail_domain",
    "yyds_api_key",
    "yyds_domain",
    "captcha_provider",
    "captcha_api_key",
    "captcha_api_base",
    "captcha_timeout",
    "captcha_poll_interval",
    "turnstile_extension_path",
    "proxy_enabled",
    "proxy_use_pool",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)


def _public_settings(config):
    return {
        "mail_provider": str(config.get("mail_provider") or "yunxin"),
        "mail_api_base": str(config.get("mail_api_base") or ""),
        "mail_api_key": str(config.get("mail_api_key") or ""),
        "mail_type": str(config.get("mail_type") or "mail"),
        "mail_suffix": str(config.get("mail_suffix") or "mail.com"),
        "mail_domain": str(config.get("mail_domain") or ""),
        "yyds_api_key": str(config.get("yyds_api_key") or ""),
        "yyds_domain": str(config.get("yyds_domain") or ""),
        "captcha_provider": str(config.get("captcha_provider") or "2captcha"),
        "captcha_api_key": str(config.get("captcha_api_key") or ""),
        "captcha_api_base": str(config.get("captcha_api_base") or "https://api.2captcha.com"),
        "captcha_timeout": int(config.get("captcha_timeout") or 180),
        "captcha_poll_interval": int(float(config.get("captcha_poll_interval") or 5)),
        "turnstile_extension_path": str(config.get("turnstile_extension_path") or ""),
        "proxy_enabled": _as_bool(config.get("proxy_enabled")),
        "proxy_use_pool": _as_bool(config.get("proxy_use_pool")),
        "http_proxy": str(config.get("http_proxy") or ""),
        "https_proxy": str(config.get("https_proxy") or ""),
        "no_proxy": str(config.get("no_proxy") or "localhost,127.0.0.1"),
    }


def _clean_url(value, field):
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith(("http://", "https://")):
        raise ValueError(f"{field} 必须以 http:// 或 https:// 开头")
    return text.rstrip("/")


def _apply_settings(payload):
    if TASK_STORE.active():
        raise RuntimeError("有注册任务正在运行，请结束后再改配置")
    current = _read_config()
    next_config = dict(current)
    data = payload if isinstance(payload, dict) else {}
    incoming = {key: data[key] for key in SETTINGS_KEYS if key in data}
    if not incoming:
        raise ValueError("没有可保存的配置项")

    merged = _public_settings({**current, **incoming})

    mail_provider = str(merged["mail_provider"] or "yunxin").strip().lower()
    if mail_provider not in {"yunxin", "yyds", "cfmail"}:
        raise ValueError("邮箱提供方只能是 yunxin、yyds 或 cfmail")
    captcha_provider = str(merged["captcha_provider"] or "2captcha").strip().lower()
    if captcha_provider not in {"2captcha", "browser"}:
        raise ValueError("打码方式只能是 2captcha 或 browser")

    timeout = int(merged["captcha_timeout"])
    poll = int(merged["captcha_poll_interval"])
    if not 30 <= timeout <= 600:
        raise ValueError("打码超时必须在 30-600 秒")
    if not 5 <= poll <= 60:
        raise ValueError("打码轮询间隔必须在 5-60 秒")

    next_config["mail_provider"] = mail_provider
    next_config["mail_api_base"] = _clean_url(merged["mail_api_base"], "邮箱 API 地址")
    next_config["mail_api_key"] = str(merged["mail_api_key"] or "").strip()
    next_config["mail_type"] = str(merged["mail_type"] or "mail").strip() or "mail"
    next_config["mail_suffix"] = str(merged["mail_suffix"] or "mail.com").strip() or "mail.com"
    next_config["mail_domain"] = str(merged["mail_domain"] or "").strip()
    next_config["yyds_api_key"] = str(merged["yyds_api_key"] or "").strip()
    next_config["yyds_domain"] = str(merged["yyds_domain"] or "").strip()
    next_config["captcha_provider"] = captcha_provider
    next_config["captcha_api_key"] = str(merged["captcha_api_key"] or "").strip()
    next_config["captcha_api_base"] = _clean_url(merged["captcha_api_base"], "打码 API 地址") or "https://api.2captcha.com"
    next_config["captcha_timeout"] = timeout
    next_config["captcha_poll_interval"] = poll
    next_config["turnstile_extension_path"] = str(merged["turnstile_extension_path"] or "").strip()
    next_config["proxy_enabled"] = _as_bool(merged["proxy_enabled"])
    next_config["proxy_use_pool"] = _as_bool(merged["proxy_use_pool"])
    next_config["http_proxy"] = str(merged["http_proxy"] or "").strip()
    next_config["https_proxy"] = str(merged["https_proxy"] or "").strip()
    next_config["no_proxy"] = str(merged["no_proxy"] or "localhost,127.0.0.1").strip() or "localhost,127.0.0.1"

    if not next_config["proxy_use_pool"] and next_config["http_proxy"]:
        _clean_url(next_config["http_proxy"], "HTTP 代理")
    if not next_config["proxy_use_pool"] and next_config["https_proxy"]:
        _clean_url(next_config["https_proxy"], "HTTPS 代理")
    if mail_provider in {"yunxin", "cfmail"} and not next_config["mail_api_base"]:
        raise ValueError("该邮箱提供方需要填写 API 地址")
    if mail_provider == "cfmail" and not next_config["mail_domain"]:
        raise ValueError("cfmail 需要填写邮箱域名")
    if captcha_provider == "2captcha" and not next_config["captcha_api_key"]:
        raise ValueError("2Captcha 需要填写 API Key")

    if next_config["proxy_use_pool"]:
        pool.registration_proxy_url(next_config)  # Validate before persisting; don't expose the URL.
        if not _as_bool(current.get("proxy_use_pool")):
            entries = pool.live_entries(_account_records(), NODE_DIR, pool.pool_settings(next_config), time.time())
            if not entries:
                raise RuntimeError("暂无可导出的节点，请先生成或导入有效代理账号")

    _atomic_json(CONFIG_FILE, next_config)
    if hasattr(worker, "reload_settings"):
        worker.reload_settings()
    return _public_settings(next_config)


CONFIG = _read_config()
WEB_USERNAME = str(os.environ.get("NODES_WEB_USERNAME") or CONFIG.get("web_username") or "admin")
WEB_PASSWORD_HASH = str(
    os.environ.get("NODES_WEB_PASSWORD_HASH") or CONFIG.get("web_password_hash") or ""
)
WEB_SESSION_SECRET = str(
    os.environ.get("NODES_WEB_SESSION_SECRET") or CONFIG.get("web_session_secret") or ""
)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = WEB_SESSION_SECRET or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=12 * 60 * 60,
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

_login_attempts = defaultdict(deque)
_login_lock = threading.Lock()


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _safe_error(error):
    text = str(error).replace("\r", " ").replace("\n", " ")
    return text[:240]


class TaskStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.tasks = self._load()
        changed = False
        for task in self.tasks:
            if task.get("status") in {"queued", "running"}:
                task["status"] = "interrupted"
                task["finished_at"] = _utc_now()
                task.setdefault("logs", []).append("Web 服务重启，任务已中断")
                changed = True
        if changed:
            self._save()

    def _load(self):
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, list) else []
        except (OSError, ValueError):
            return []

    def _save(self):
        _atomic_json(self.path, self.tasks[:200])

    def _find(self, task_id):
        return next((item for item in self.tasks if item.get("id") == task_id), None)

    def list(self, limit=50):
        with self.lock:
            return json.loads(json.dumps(self.tasks[:limit]))

    def get(self, task_id):
        with self.lock:
            task = self._find(task_id)
            return json.loads(json.dumps(task)) if task else None

    def active(self):
        with self.lock:
            return next(
                (item for item in self.tasks if item.get("status") in {"queued", "running"}),
                None,
            )

    def start_task(self, count, concurrency):
        with self.lock:
            if self.active():
                raise RuntimeError("已有注册任务在运行")
            task_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
            task = {
                "id": task_id,
                "status": "queued",
                "requested": count,
                "concurrency": concurrency,
                "completed": 0,
                "successes": 0,
                "partials": 0,
                "failures": 0,
                "proxy_count": 0,
                "created_at": _utc_now(),
                "started_at": None,
                "finished_at": None,
                "account_file": f"accounts_{task_id}.jsonl",
                "proxy_file": f"proxies_{task_id}.txt",
                "logs": [f"任务已创建：{count} 个账号，并发 {concurrency}"],
            }
            self.tasks.insert(0, task)
            self._save()
        thread = threading.Thread(target=self._run, args=(task_id,), daemon=True)
        thread.start()
        return self.get(task_id)

    def _update(self, task_id, **values):
        with self.lock:
            task = self._find(task_id)
            if not task:
                return
            task.update(values)
            self._save()

    def _event(self, task_id, message):
        with self.lock:
            task = self._find(task_id)
            if not task:
                return
            task.setdefault("logs", []).append(message[:300])
            task["logs"] = task["logs"][-100:]
            self._save()

    def _run(self, task_id):
        task = self.get(task_id)
        if not task:
            return
        self._update(task_id, status="running", started_at=_utc_now())
        self._event(task_id, "注册 worker 已启动")
        account_path = str(ACCOUNT_DIR / task["account_file"])
        proxy_path = str(NODE_DIR / task["proxy_file"])

        def execute(index):
            return worker.register_one(index, True, account_path, proxy_path)

        with ThreadPoolExecutor(max_workers=task["concurrency"]) as executor:
            futures = {
                executor.submit(execute, index): index
                for index in range(1, task["requested"] + 1)
            }
            for future in as_completed(futures):
                index = futures[future]
                success = partial = False
                proxies = 0
                try:
                    record = future.result()
                    proxies = int((record or {}).get("proxy_count") or 0)
                    success = proxies > 0
                    partial = bool(record) and not success
                    message = (
                        f"#{index} 完成，导出 {proxies} 个代理"
                        if success
                        else f"#{index} 未完整产出，已保留诊断记录"
                    )
                except Exception as error:
                    message = f"#{index} 失败：{_safe_error(error)}"
                with self.lock:
                    current = self._find(task_id)
                    if not current:
                        return
                    current["completed"] += 1
                    current["successes"] += int(success)
                    current["partials"] += int(partial)
                    current["failures"] += int(not success and not partial)
                    current["proxy_count"] += proxies
                    current.setdefault("logs", []).append(message)
                    self._save()

        final = self.get(task_id)
        if final["successes"] == final["requested"]:
            status = "success"
        elif final["successes"] > 0 or final["partials"] > 0:
            status = "partial"
        else:
            status = "failed"
        self._update(task_id, status=status, finished_at=_utc_now())
        self._event(task_id, f"任务结束：{final['successes']}/{final['requested']} 成功")


TASK_STORE = TaskStore(TASKS_FILE)
_POOL_LOOP_STARTED = False
_POOL_LOOP_LOCK = threading.Lock()


def _is_authenticated():
    return session.get("authenticated") is True and session.get("username") == WEB_USERNAME


def _csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _too_many_logins(address):
    now = time.monotonic()
    with _login_lock:
        attempts = _login_attempts[address]
        while attempts and attempts[0] < now - 600:
            attempts.popleft()
        return len(attempts) >= 8


def _record_failed_login(address):
    with _login_lock:
        _login_attempts[address].append(time.monotonic())


def _export_token():
    token = str(os.environ.get("NODES_EXPORT_TOKEN") or "").strip()
    if token:
        return token
    config = _read_config()
    token = str(config.get("export_token") or "").strip()
    if token:
        return token
    if app.config.get("TESTING"):
        return "test-export-token"
    token = secrets.token_urlsafe(24)
    next_config = dict(config)
    next_config["export_token"] = token
    _atomic_json(CONFIG_FILE, next_config)
    return token


def _token_matches(supplied, expected):
    left = str(supplied or "")
    right = str(expected or "")
    if not left or not right or len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def _request_export_token_candidates():
    values = []
    header = str(request.headers.get("X-Export-Token") or "").strip()
    if header:
        values.append(header)
    auth = str(request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        values.append(auth[7:].strip())
    query = str(request.args.get("token") or "").strip()
    if query:
        values.append(query)
    view = str((request.view_args or {}).get("export_token") or "").strip()
    if view:
        values.append(view)
    return values


def _request_has_export_token():
    expected = _export_token()
    return any(_token_matches(value, expected) for value in _request_export_token_candidates())


def _can_export_clash():
    return _request_has_export_token() or _is_authenticated()


def _pool_snapshot():
    settings = pool.pool_settings(_read_config())
    entries = pool.live_entries(_account_records(), NODE_DIR, settings, time.time())
    cap = pool.capacity(entries, settings)
    cap["accounts"] = [
        {"email": item["email"], "slots": len(item["slots"])}
        for item in entries
    ]
    return settings, entries, cap


def _live_proxy_body():
    _settings, entries, _cap = _pool_snapshot()
    lines = pool.format_proxy_lines(entries, worker._format_proxy_url)
    return "\n".join(lines) + ("\n" if lines else "")


def _public_base():
    root = (request.url_root or "").rstrip("/")
    script = (request.script_root or "").rstrip("/")
    if script and (root == script or root.endswith(script)):
        return root
    return root + script


def _subscription_urls():
    token = _export_token()
    public_base = _public_base()
    internal_base = str(_read_config().get("internal_base_url") or "http://127.0.0.1:8891/nodes").rstrip("/")
    internal = f"{internal_base}/api/export/live-proxies?token={token}"
    public = f"{public_base}/api/export/live-proxies?token={token}"
    gpt_public = f"{public_base}/api/export/gpt-gateway?token={token}"
    gpt_internal = f"{internal_base}/api/export/gpt-gateway?token={token}"
    clash_public = f"{public_base}/api/export/clash.yml?token={token}"
    ladder_public = f"{public_base}/api/export/ladder?token={token}"
    return {
        "resin_internal": internal,
        "resin_public": public,
        "gpt_internal": gpt_internal,
        "gpt_public": gpt_public,
        "clash_public": clash_public,
        "ladder_public": ladder_public,
    }


def _gpt_gateway_body(settings, live_slots):
    token, auth_version = pool.resin_auth(_read_config())
    if not token:
        return ""
    return "\n".join(pool.gpt_gateway_lines(
        int(live_slots),
        token,
        settings["gateway_host"],
        settings["gateway_port"],
        auth_version,
        settings["gateway_platform"],
    )) + "\n"


def _maybe_fill_capacity(auto_register=True):
    settings, _entries, cap = _pool_snapshot()
    started = None
    if auto_register and settings["auto_register"] and cap["needed_accounts"] > 0 and not TASK_STORE.active():
        count = min(int(settings["max_register_per_round"]), int(cap["needed_accounts"]))
        started = TASK_STORE.start_task(count, 1)
        cap["register_started"] = started.get("id") if started else None
        cap["register_count"] = count
    else:
        cap["register_started"] = None
        cap["register_count"] = 0
    cap["active_task"] = (TASK_STORE.active() or {}).get("id")
    return cap, started


def _start_pool_loop():
    global _POOL_LOOP_STARTED
    if app.config.get("TESTING") or os.environ.get("NODES_DISABLE_POOL_LOOP") == "1":
        return
    with _POOL_LOOP_LOCK:
        if _POOL_LOOP_STARTED:
            return
        _POOL_LOOP_STARTED = True

    def _loop():
        while True:
            try:
                settings = pool.pool_settings(_read_config())
                time.sleep(int(settings["loop_seconds"]))
                _maybe_fill_capacity(auto_register=True)
            except Exception:
                time.sleep(30)

    threading.Thread(target=_loop, name="nodes-pool-loop", daemon=True).start()


@app.before_request
def protect_routes():
    _start_pool_loop()
    if request.endpoint in {"login", "health", "static", "live_proxies", "gpt_gateway", "clash_export", "ladder_export"}:
        return None
    if request.endpoint == "ensure_capacity" and _request_has_export_token():
        return None
    if not _is_authenticated():
        if request.path.startswith("/api/") or request.path.startswith("/export/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login", next=request.path))
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(supplied, session.get("csrf_token", "")):
            return jsonify({"error": "invalid_csrf"}), 403
    return None


@app.after_request
def secure_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    if _is_authenticated():
        return redirect(url_for("index"))
    error = None
    username = ""
    if request.method == "POST":
        address = request.remote_addr or "unknown"
        if _too_many_logins(address):
            error = "尝试过于频繁，请稍后再试"
        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            valid = bool(WEB_PASSWORD_HASH) and check_password_hash(WEB_PASSWORD_HASH, password)
            if hmac.compare_digest(username, WEB_USERNAME) and valid:
                session.clear()
                session.permanent = True
                session["authenticated"] = True
                session["username"] = WEB_USERNAME
                _csrf_token()
                return redirect(url_for("index"))
            _record_failed_login(address)
            error = "用户名或密码不正确"
    return render_template("login.html", error=error, username=username)


@app.get("/")
def index():
    return render_template(
        "index.html",
        csrf_token=_csrf_token(),
        username=WEB_USERNAME,
        app_base=request.script_root or "",
    )


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "nodes-dashboard"})


@app.get("/api/export/live-proxies")
def live_proxies():
    if not _request_has_export_token():
        return jsonify({"error": "unauthorized"}), 401
    body = _live_proxy_body()
    return Response(body, mimetype="text/plain; charset=utf-8")


@app.get("/api/export/gpt-gateway")
def gpt_gateway():
    if not _request_has_export_token():
        return jsonify({"error": "unauthorized"}), 401
    settings, _entries, cap = _pool_snapshot()
    body = _gpt_gateway_body(settings, cap["live_slots"])
    if not body.strip():
        return jsonify({"error": "resin_proxy_token_missing"}), 503
    return Response(body, mimetype="text/plain; charset=utf-8")


def _clash_body(settings, live_slots):
    token, auth_version = pool.resin_auth(_read_config())
    if not token:
        return ""
    return pool.clash_yaml(
        int(live_slots),
        token,
        settings["gateway_host"],
        settings["gateway_port"],
        auth_version,
        settings["gateway_platform"],
    )


def _ladder_body(settings, live_slots):
    token, auth_version = pool.resin_auth(_read_config())
    if not token:
        return ""
    return pool.ladder_base64(
        int(live_slots),
        token,
        settings["gateway_host"],
        settings["gateway_port"],
        auth_version,
        settings["gateway_platform"],
    )


@app.get("/api/export/clash.yml")
@app.get("/api/export/clash")
@app.get("/api/export/clash.yml/<export_token>")
@app.get("/api/export/clash/<export_token>")
def clash_export(export_token=None):
    if not _can_export_clash():
        return jsonify({"error": "unauthorized"}), 401
    settings, _entries, cap = _pool_snapshot()
    body = _clash_body(settings, cap["live_slots"])
    if not body.strip():
        return jsonify({"error": "resin_proxy_token_missing"}), 503
    response = Response(body, mimetype="text/yaml; charset=utf-8")
    response.headers["Content-Disposition"] = 'attachment; filename="clash.yml"'
    response.headers["Profile-Update-Interval"] = "1"
    response.headers["Subscription-Userinfo"] = (
        f"upload=0; download=0; total=0; expire={int(time.time()) + 7 * 86400}"
    )
    return response


@app.get("/api/export/ladder")
def ladder_export():
    if not _request_has_export_token():
        return jsonify({"error": "unauthorized"}), 401
    settings, _entries, cap = _pool_snapshot()
    body = _ladder_body(settings, cap["live_slots"])
    if not body.strip():
        return jsonify({"error": "resin_proxy_token_missing"}), 503
    return Response(body, mimetype="text/plain; charset=utf-8")


@app.get("/api/pool")
def pool_status():
    return jsonify({"pool": _pool_public()})


@app.post("/api/pool/ensure-capacity")
def ensure_capacity():
    payload = request.get_json(silent=True) or {}
    auto_register = True if "auto_register" not in payload else bool(payload.get("auto_register"))
    try:
        cap, started = _maybe_fill_capacity(auto_register=auto_register)
    except RuntimeError as error:
        return jsonify({"error": str(error), "pool": _pool_public()}), 409
    return jsonify({
        "ok": True,
        "pool": _pool_public(),
        "capacity": cap,
        "task": started,
    })


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


def _deleted_emails_file():
    return ACCOUNT_DIR / "deleted_emails.json"


def _load_deleted_emails():
    try:
        value = json.loads(_deleted_emails_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if isinstance(value, list):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    return set()


def _save_deleted_emails(emails):
    unique = sorted({str(item).strip().lower() for item in emails if str(item).strip()})
    _atomic_json(_deleted_emails_file(), unique)


def _record_stamp(record):
    for key in ("updated_at", "imported_at", "usage_synced_at", "ts"):
        try:
            value = int(record.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return 0


def _account_records():
    deleted = _load_deleted_emails()
    latest = {}
    for path in sorted(ACCOUNT_DIR.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    email = str(record.get("email") or "").strip()
                    key = email.lower()
                    if not email or key in deleted:
                        continue
                    previous = latest.get(key)
                    if previous is None or _record_stamp(record) >= _record_stamp(previous):
                        latest[key] = record
        except OSError:
            continue
    return list(latest.values())


def _proxy_files():
    files = []
    for path in sorted(NODE_DIR.glob("*.txt"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                count = sum(1 for line in handle if line.strip())
            stat = path.stat()
            files.append({
                "name": path.name,
                "count": count,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            })
        except OSError:
            continue
    return files


def _public_accounts(records, full=False):
    result = []
    for record in sorted(records, key=lambda item: int(item.get("ts") or 0), reverse=True):
        result.append(_serialize_account(record, full=full))
    return result


def _account_map():
    latest = {}
    for record in _account_records():
        email = str(record.get("email") or "").strip().lower()
        if email:
            latest[email] = record
    return latest


def _find_account(email):
    return _account_map().get(str(email or "").strip().lower())


def _iso_from_unix(value):
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def _api_key_blob(record):
    blob = record.get("api_key")
    return dict(blob) if isinstance(blob, dict) else {}


def _account_api_urls(record):
    account_id = str(record.get("account_id") or "").strip()
    api_key = _api_key_blob(record)
    token = str(api_key.get("token") or record.get("api_token") or "").strip()
    user = str(record.get("proxy_username") or "").strip()
    password = str(record.get("proxy_password") or "").strip()
    public_list = ""
    dashboard_list = ""
    dashboard_overview = ""
    if account_id:
        public_list = (
            f"{worker.PS_PUBLIC_API}/v4/account/{account_id}/datacenter_shared/proxy-list"
            "?protocol=http&format=credentials&credential_format=3"
        )
        dashboard_list = (
            f"{worker.PS_BASE}/v2/v4/account/{account_id}/datacenter_shared/proxy-list"
            "?protocol=http&format=normal"
        )
        dashboard_overview = f"{worker.PS_BASE}/v2/v4/account/{account_id}/services/overview"
    return {
        "public_base": worker.PS_PUBLIC_API,
        "public_proxy_list": public_list,
        "dashboard_overview": dashboard_overview,
        "dashboard_proxy_list": dashboard_list,
        "api_token_header": "api-token",
        "dashboard_auth_header": "Authorization: Bearer <access_token>",
        "proxy_credential": f"{user}:{password}" if user and password else "",
        "proxy_url_template": f"http://{user}:{password}@HOST:PORT" if user and password else "",
        "curl_public_proxy_list": (
            f"curl -H \"api-token: {token}\" \"{public_list}\"" if token and public_list else ""
        ),
        "has_token": bool(token),
    }


def _usage_fields(record):
    summary = record.get("account_summary") if isinstance(record.get("account_summary"), dict) else {}
    expiry = record.get("expiration_time") or record.get("expiry") or summary.get("expiry")
    try:
        expiry = int(expiry) if expiry else None
    except (TypeError, ValueError):
        expiry = None
    total = record.get("bandwidth_total")
    if total is None:
        total = record.get("bandwidth")
    if total is None:
        total = summary.get("bandwidth_total")
    used = record.get("bandwidth_used")
    if used is None:
        used = summary.get("bandwidth_used")
    try:
        total = int(total) if total is not None else None
    except (TypeError, ValueError):
        total = None
    try:
        used = int(used) if used is not None else None
    except (TypeError, ValueError):
        used = None
    remaining = record.get("bandwidth_remaining")
    if remaining is None and total is not None and used is not None:
        remaining = max(0, total - used)
    try:
        remaining = int(remaining) if remaining is not None else None
    except (TypeError, ValueError):
        remaining = None
    days = record.get("days_remaining")
    if days is None:
        days = summary.get("days_remaining")
    if days is None and expiry:
        days = max(0, int((expiry - time.time()) // 86400))
    try:
        days = int(days) if days is not None else None
    except (TypeError, ValueError):
        days = None
    return {
        "expires_at": _iso_from_unix(expiry),
        "expiry": expiry,
        "days_remaining": days,
        "expired": bool(expiry and expiry <= time.time()),
        "bandwidth_total": total,
        "bandwidth_used": used if used is not None else (0 if total is not None else None),
        "bandwidth_remaining": remaining,
        "usage_synced_at": _iso_from_unix(record.get("usage_synced_at")),
        "plan_status": summary.get("status") or record.get("plan_status") or "",
    }


def _serialize_account(record, full=False):
    api_key = _api_key_blob(record)
    token = str(api_key.get("token") or record.get("api_token") or "").strip()
    item = {
        "email": record.get("email") or "",
        "verified": bool(record.get("verified")),
        "trial_claimed": bool(record.get("trial_claimed")),
        "proxy_count": int(record.get("proxy_count") or 0),
        "account_id": record.get("account_id") or "",
        "has_api_key": bool(token),
        "has_proxy_credentials": bool(record.get("proxy_username")),
        "is_trial": record.get("is_trial"),
        "created_at": _iso_from_unix(record.get("ts")),
    }
    item.update(_usage_fields(record))
    if not full:
        return item
    item.update({
        "password": record.get("password") or "",
        "access_token": record.get("access_token") or "",
        "proxy_username": record.get("proxy_username") or "",
        "proxy_password": record.get("proxy_password") or "",
        "account_key": record.get("account_key") or "",
        "api_token": token,
        "api_key_id": api_key.get("id") or record.get("api_key_id") or "",
        "api_key_name": api_key.get("name") or record.get("api_key_name") or worker.API_KEY_NAME,
        "permissions": api_key.get("permissions") or record.get("permissions") or [],
        "allowed_subaccounts": api_key.get("allowed_subaccounts") or record.get("allowed_subaccounts") or [],
        "allowed_ips": api_key.get("allowed_ips") or record.get("allowed_ips") or [],
        "proxy_credentials_enabled": record.get("proxy_credentials_enabled"),
        "max_connections": record.get("max_connections"),
        "proxy_amount": record.get("proxy_amount"),
        "notes": record.get("notes") or "",
        "userData": record.get("userData") or {},
        "account_summary": record.get("account_summary"),
        "api": _account_api_urls(record),
        "permission_catalog": worker.PERMISSION_CATALOG,
    })
    return item


def _pool_public():
    settings, _entries, cap = _pool_snapshot()
    urls = _subscription_urls()
    resin_token, auth_version = pool.resin_auth(_read_config())
    if resin_token:
        user, password = pool.gateway_identity(
            1, auth_version, settings["gateway_platform"], resin_token,
        )
        gateway_sample = (
            f"http://{user}:{password}@{settings['gateway_host']}:{settings['gateway_port']}"
        )
    else:
        gateway_sample = (
            f"http://{settings['gateway_platform']}.n01:<RESIN_PROXY_TOKEN>"
            f"@{settings['gateway_host']}:{settings['gateway_port']}"
        )
    return {
        **cap,
        "subscription_url": urls["resin_internal"],
        "subscription_url_public": urls["resin_public"],
        "gpt_subscription_url": urls["gpt_public"],
        "clash_subscription_url": urls["clash_public"],
        "ladder_subscription_url": urls["ladder_public"],
        "gpt_gateway_sample": gateway_sample,
        "gpt_gateway_host": f"{settings['gateway_host']}:{settings['gateway_port']}",
        "auth_version": auth_version,
        "has_resin_token": bool(resin_token),
    }


def _save_account_record(record):
    email = str(record.get("email") or "").strip().lower()
    if email:
        deleted = _load_deleted_emails()
        if email in deleted:
            deleted.discard(email)
            _save_deleted_emails(deleted)
    path = ACCOUNT_DIR / "accounts_edits.jsonl"
    worker.save_account(record, str(path))
    return record


def _normalize_email_list(values):
    result = []
    seen = set()
    for item in values or []:
        email = str(item or "").strip()
        key = email.lower()
        if not email or key in seen:
            continue
        seen.add(key)
        result.append(email)
    return result


def _delete_accounts(emails):
    wanted = {item.lower() for item in _normalize_email_list(emails)}
    if not wanted:
        raise ValueError("没有要删除的账号")
    existing = _account_map()
    removed = [existing[key].get("email") or key for key in wanted if key in existing]
    if not removed:
        return []
    deleted = _load_deleted_emails()
    deleted.update(item.lower() for item in removed)
    _save_deleted_emails(deleted)
    return removed


def _parse_account_text(text):
    payload = str(text or "").strip()
    if not payload:
        return []
    try:
        data = json.loads(payload)
    except ValueError:
        data = None
    if isinstance(data, dict):
        if isinstance(data.get("accounts"), list):
            return data["accounts"]
        return [data]
    if isinstance(data, list):
        return data
    items = []
    for index, line in enumerate(payload.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError as error:
            raise ValueError(f"第 {index} 行不是 JSON") from error
        items.append(item)
    return items


def _normalize_imported_record(raw):
    if not isinstance(raw, dict):
        raise ValueError("账号必须是 JSON 对象")
    email = str(raw.get("email") or "").strip()
    if "@" not in email:
        raise ValueError("缺少有效邮箱")
    record = dict(raw)
    record["email"] = email
    if record.get("api_token") and not isinstance(record.get("api_key"), dict):
        record["api_key"] = {
            "token": str(record.get("api_token") or "").strip(),
            "id": str(record.get("api_key_id") or "").strip(),
            "name": str(record.get("api_key_name") or worker.API_KEY_NAME).strip() or worker.API_KEY_NAME,
            "permissions": record.get("permissions") or [],
            "allowed_subaccounts": record.get("allowed_subaccounts") or [],
            "allowed_ips": record.get("allowed_ips") or [],
        }
    try:
        record["proxy_count"] = int(record.get("proxy_count") or 0)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{email} 的代理数无效") from error
    record["verified"] = _as_bool(record.get("verified"))
    record["trial_claimed"] = _as_bool(record.get("trial_claimed"))
    record["ts"] = int(record.get("ts") or time.time())
    record["imported_at"] = int(time.time())
    return record


def _import_accounts(items):
    if not isinstance(items, list) or not items:
        raise ValueError("没有可导入的账号")
    if len(items) > 500:
        raise ValueError("单次最多导入 500 个账号")
    imported = []
    restored = []
    deleted = _load_deleted_emails()
    changed_deleted = False
    for raw in items:
        record = _normalize_imported_record(raw)
        email_key = record["email"].lower()
        existing = _find_account(record["email"])
        if existing:
            merged = dict(existing)
            merged.update(record)
            record = merged
        elif email_key in deleted:
            deleted.discard(email_key)
            changed_deleted = True
            restored.append(record["email"])
        _save_account_record(record)
        imported.append(record["email"])
    if changed_deleted:
        _save_deleted_emails(deleted)
    return {"imported": imported, "restored": restored}


def _apply_account_update(record, payload):
    data = payload if isinstance(payload, dict) else {}
    next_record = dict(record)
    text_fields = (
        "password", "access_token", "account_id", "proxy_username", "proxy_password",
        "account_key", "notes",
    )
    for key in text_fields:
        if key in data:
            next_record[key] = str(data.get(key) or "").strip()
    if "proxy_count" in data:
        try:
            next_record["proxy_count"] = int(data.get("proxy_count") or 0)
        except (TypeError, ValueError) as error:
            raise ValueError("代理数必须是整数") from error
    if "verified" in data:
        next_record["verified"] = _as_bool(data.get("verified"))
    if "trial_claimed" in data:
        next_record["trial_claimed"] = _as_bool(data.get("trial_claimed"))
    api_key = _api_key_blob(next_record)
    if "api_token" in data:
        api_key["token"] = str(data.get("api_token") or "").strip()
    if "api_key_id" in data:
        api_key["id"] = str(data.get("api_key_id") or "").strip()
    if "api_key_name" in data:
        api_key["name"] = str(data.get("api_key_name") or "").strip() or worker.API_KEY_NAME
    if "permissions" in data:
        raw = data.get("permissions") or []
        if not isinstance(raw, list):
            raise ValueError("权限必须是数组")
        api_key["permissions"] = [str(item).strip() for item in raw if str(item).strip()]
    if "allowed_subaccounts" in data:
        raw = data.get("allowed_subaccounts") or []
        if not isinstance(raw, list):
            raise ValueError("allowed_subaccounts 必须是数组")
        api_key["allowed_subaccounts"] = [str(item).strip() for item in raw if str(item).strip()]
    if "allowed_ips" in data:
        raw = data.get("allowed_ips") or []
        if not isinstance(raw, list):
            raise ValueError("allowed_ips 必须是数组")
        api_key["allowed_ips"] = [str(item).strip() for item in raw if str(item).strip()]
    if api_key:
        next_record["api_key"] = api_key
    next_record["ts"] = int(record.get("ts") or time.time())
    next_record["updated_at"] = int(time.time())
    return next_record


def _ps_session():
    session_obj = requests.Session()
    session_obj.headers.update(worker.HEADERS)
    return session_obj


def _refresh_account_remote(record, create_key=False, permissions=None):
    access_token = str(record.get("access_token") or "").strip()
    account_id = str(record.get("account_id") or "").strip()
    if not access_token:
        raise RuntimeError("账号没有 access_token，无法向 ProxyScrape 拉数据")
    if not account_id:
        raise RuntimeError("账号没有 account_id，无法向 ProxyScrape 拉数据")
    next_record = dict(record)
    overview = worker.fetch_service_overview(access_token, account_id)
    next_record.update(worker.overview_credentials(overview))
    client = _ps_session()
    try:
        summary = worker.fetch_accounts_summary(client, access_token)
        next_record["account_summary"] = next(
            (item for item in summary if item.get("id") == account_id),
            summary[0] if summary else None,
        )
    except Exception as error:
        next_record["account_summary_error"] = str(error)[:240]
    summary = next_record.get("account_summary") if isinstance(next_record.get("account_summary"), dict) else {}
    if summary.get("expiry") and not next_record.get("expiration_time"):
        next_record["expiration_time"] = summary.get("expiry")
    if summary.get("days_remaining") is not None:
        next_record["days_remaining"] = summary.get("days_remaining")
    if summary.get("status"):
        next_record["plan_status"] = summary.get("status")
    next_record["usage_synced_at"] = int(time.time())
    try:
        plist = worker.list_proxy_hosts(access_token, account_id)
        if plist:
            next_record["proxy_ips"] = plist
            next_record["proxy_count"] = len(plist)
    except Exception as error:
        next_record["proxy_list_error"] = str(error)[:240]
    if create_key:
        perms = permissions if permissions is not None else (
            _api_key_blob(next_record).get("permissions") or list(worker.DEFAULT_API_PERMISSIONS)
        )
        if not perms:
            raise ValueError("至少勾选一项 API 权限")
        next_record["api_key"] = worker.provision_api_key(
            client, access_token, account_id,
            permissions=perms,
            name=_api_key_blob(next_record).get("name") or worker.API_KEY_NAME,
            existing=_api_key_blob(next_record),
        )
    next_record["updated_at"] = int(time.time())
    return next_record


@app.get("/api/dashboard")
def dashboard():
    records = _account_records()
    files = _proxy_files()
    active = TASK_STORE.active()
    config = _read_config()
    return jsonify({
        "summary": {
            "accounts": len(records),
            "verified": sum(1 for item in records if item.get("verified")),
            "proxies": sum(item["count"] for item in files),
            "successful_accounts": sum(1 for item in records if int(item.get("proxy_count") or 0) > 0),
        },
        "chain": {
            "captcha": str(config.get("captcha_provider") or worker.CAPTCHA_PROVIDER),
            "mail": str(config.get("mail_provider") or worker.MAIL_PROVIDER),
            "proxy": "enabled" if (_as_bool(config.get("proxy_enabled")) or _as_bool(config.get("proxy_use_pool"))) else ("configured" if files else "waiting"),
            "output": "enabled",
        },
        "active_task": active.get("id") if active else None,
        "tasks": TASK_STORE.list(8),
        "pool": _pool_public(),
    })


@app.get("/api/accounts")
def accounts():
    return jsonify({"accounts": _public_accounts(_account_records())[:500]})


@app.get("/api/permission-catalog")
def permission_catalog():
    return jsonify(worker.PERMISSION_CATALOG)


@app.post("/api/accounts/import")
def import_accounts():
    payload = request.get_json(silent=True) or {}
    try:
        if isinstance(payload.get("accounts"), list):
            items = payload["accounts"]
        else:
            items = _parse_account_text(payload.get("text") or "")
        result = _import_accounts(items)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({
        "ok": True,
        "imported": len(result["imported"]),
        "restored": len(result["restored"]),
        "emails": result["imported"],
        "accounts": _public_accounts(_account_records())[:500],
    })


@app.post("/api/accounts/delete")
def delete_accounts():
    payload = request.get_json(silent=True) or {}
    emails = payload.get("emails") if isinstance(payload.get("emails"), list) else []
    if payload.get("email"):
        emails = list(emails) + [payload.get("email")]
    try:
        removed = _delete_accounts(emails)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if not removed:
        return jsonify({"error": "没有找到要删除的账号"}), 404
    return jsonify({"ok": True, "deleted": removed, "accounts": _public_accounts(_account_records())[:500]})


@app.post("/api/accounts/sync-usage")
def sync_accounts_usage():
    payload = request.get_json(silent=True) or {}
    emails = _normalize_email_list(payload.get("emails") or [])
    records = _account_records()
    if emails:
        wanted = {item.lower() for item in emails}
        records = [item for item in records if str(item.get("email") or "").lower() in wanted]
    synced = []
    failed = []
    for record in records:
        email = record.get("email") or ""
        if not record.get("access_token") or not record.get("account_id"):
            failed.append({"email": email, "error": "缺少 access_token 或 account_id"})
            continue
        try:
            updated = _refresh_account_remote(record, create_key=False)
            _save_account_record(updated)
            synced.append(email)
        except Exception as error:
            failed.append({"email": email, "error": _safe_error(error)})
    return jsonify({
        "ok": True,
        "synced": synced,
        "failed": failed,
        "accounts": _public_accounts(_account_records())[:500],
    })


@app.get("/api/accounts/<path:email>")
def account_detail(email):
    record = _find_account(email)
    if not record:
        abort(404)
    return jsonify({"account": _serialize_account(record, full=True)})


@app.delete("/api/accounts/<path:email>")
def delete_account(email):
    try:
        removed = _delete_accounts([email])
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if not removed:
        abort(404)
    return jsonify({"ok": True, "deleted": removed})


@app.put("/api/accounts/<path:email>")
def update_account(email):
    record = _find_account(email)
    if not record:
        abort(404)
    payload = request.get_json(silent=True) or {}
    try:
        next_record = _apply_account_update(record, payload)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    _save_account_record(next_record)
    return jsonify({"ok": True, "account": _serialize_account(next_record, full=True)})


@app.post("/api/accounts/<path:email>/refresh")
def refresh_account(email):
    record = _find_account(email)
    if not record:
        abort(404)
    try:
        next_record = _refresh_account_remote(record, create_key=False)
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409
    except Exception as error:
        return jsonify({"error": _safe_error(error)}), 502
    _save_account_record(next_record)
    return jsonify({"ok": True, "account": _serialize_account(next_record, full=True)})


@app.post("/api/accounts/<path:email>/api-key")
def sync_account_api_key(email):
    record = _find_account(email)
    if not record:
        abort(404)
    payload = request.get_json(silent=True) or {}
    try:
        staged = _apply_account_update(record, payload)
        permissions = staged.get("api_key", {}).get("permissions") if isinstance(staged.get("api_key"), dict) else None
        next_record = _refresh_account_remote(staged, create_key=True, permissions=permissions)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409
    except Exception as error:
        return jsonify({"error": _safe_error(error)}), 502
    _save_account_record(next_record)
    return jsonify({"ok": True, "account": _serialize_account(next_record, full=True)})


@app.get("/api/exports")
def exports():
    account_files = []
    for path in sorted(ACCOUNT_DIR.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        account_files.append({
            "name": path.name,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    return jsonify({"accounts": account_files, "proxies": _proxy_files()})


@app.get("/api/tasks")
def tasks():
    return jsonify({"tasks": TASK_STORE.list(100)})


@app.post("/api/tasks")
def create_task():
    payload = request.get_json(silent=True) or {}
    try:
        count = int(payload.get("count", 1))
        concurrency = int(payload.get("concurrency", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "注册数量和并发数必须为整数"}), 400
    if not 1 <= count <= 100:
        return jsonify({"error": "注册数量必须在 1-100 之间"}), 400
    if not 1 <= concurrency <= min(8, count):
        return jsonify({"error": "并发数必须在 1-8 之间，且不能超过注册数量"}), 400
    try:
        task = TASK_STORE.start_task(count, concurrency)
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409
    return jsonify({"task": task}), 202


@app.get("/api/tasks/<task_id>")
def task_detail(task_id):
    task = TASK_STORE.get(task_id)
    if not task:
        abort(404)
    return jsonify({"task": task})


@app.get("/api/settings")
def get_settings():
    return jsonify({"settings": _public_settings(_read_config())})


@app.put("/api/settings")
def put_settings():
    payload = request.get_json(silent=True) or {}
    try:
        settings = _apply_settings(payload)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409
    return jsonify({"ok": True, "settings": settings})


@app.get("/download/<kind>/<path:filename>")
def download(kind, filename):
    if Path(filename).name != filename:
        abort(404)
    if kind == "accounts" and filename.endswith(".jsonl"):
        directory = ACCOUNT_DIR
    elif kind == "proxies" and filename.endswith(".txt"):
        directory = NODE_DIR
    else:
        abort(404)
    return send_from_directory(directory, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
