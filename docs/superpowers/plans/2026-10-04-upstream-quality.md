# Selective upstream quality integration

**Goal:** Port proxy quality, inventory history and rule management from upstream `f140ea8` onto the deployed Nodes snapshot without replacing its mail, export or runtime fixes.

**Architecture:** Adapt `proxy_quality.py` and `platform_store.py` from upstream. Add profile helpers in `quality_rules.py`, authenticated APIs in `web_app.py`, and two views with a separate frontend controller. Existing exports retain their meaning; `/api/export/qualified-proxies` is a new opt-in filtered export of local live proxies. Requests which just read state never launch probes. Explicit check/dry-run actions probe at most 50 URLs per request; checking inventory uses background batches and reports progress. No external proxy source integration, automatic pruning or registration changes in this task.

**Tech stack:** Flask, requests, Python 3.12, plain JS, existing Docker/Caddy deployment.

**Spec:** User approved selective integration following the comparison in chat; preserve `cfmail`, 100-node exports, Resin authentication, task diagnostics and Xvfb repair. Default filter off, no country exclusion, generic HTTPS target connectivity probe. No live registrations for validation.

## Global constraints and interfaces

- Worktree baseline: `a6d93ed`; main workspace retains prior uncommitted edits until integration is verified.
- Never print production credentials. Config and new state files use atomic writes with 0600 permissions. Reports and cache contain redacted proxy endpoints, never raw credentials.
- `proxy_quality.quality_settings(config)` normalizes existing upstream field names: proxy_quality_enabled (False), proxy_max_latency_ms (3000), proxy_exclude_countries (empty), proxy_arp_check_enabled (False), proxy_arp_probe_url (https://cp.cloudflare.com/generate_204), proxy_quality_workers (8), proxy_quality_cache_ttl_sec (600), proxy_quality_timeout_sec (12).
- `filter_proxies(urls, settings=None, data_dir=None, use_cache=True, probe_missing=True)` returns `(accepted_urls, report)`. Report: scanned, accepted, rejected, skipped_unprobed, enabled, results. Untested is not failed; `results[].ok` is null when untested. Disabled returns input URLs, zero tested/accepted/rejected, all untested. Preserve URL order, duplicates removed.
- Probe reports: identity (endpoint without auth), ok, latency_ms, country, country_code, egress_ip, arp_ok, arp_status, reason, checked_at. Cache identities depend on full authenticated URL AND check-affecting rules; credential/rule changes cannot reuse results. `probe_missing=False` is a network-free read. `use_cache=False` is a dry-run and must not write cache.
- Storage exports: save_inventory_snapshot(data_dir, inventory, min_interval_sec=300), load_inventory_latest(data_dir), load_inventory_history(data_dir, limit=48), append_audit(data_dir, kind, action, detail=None, actor='web'), load_audit(data_dir, kind=None, limit=50).
- API: GET `/api/quality/profiles` -> `{ok,active_id,export_id,profiles}`. Profile rows: id/name/version/updated_at plus flat quality keys. PUT same `/id` upserts; POST `/activate` selects id. GET `/api/inventory` -> `{ok, inventory, latest, check, qualified_url}` where inventory has scanned,accepted,rejected,skipped_unprobed,enabled,reasons,results,profile_id,version; check has running,completed,total,error. POST `/api/quality/check` starts a background check; 409 when one is running. POST `/api/quality/dry-run` accepts `{text,rule}` and returns report; no persistent change. POST `/api/inventory/snapshot`; GET `/api/inventory/history`; GET `/api/audit`. New qualified export requires login or export token and uses cached passing nodes (no automatic network I/O).

## Task 1: Adapt upstream engine and private storage

Files: proxy_quality.py, platform_store.py, test_quality_engine.py.
- [x] Write tests: default no probes/exclusions, distinct credential/rule caches, concurrent writes, dry-run isolation, 0600, HTTP failures rejected, untested separate, URL order retained.
- [x] Demonstrate failures before porting, then port relevant upstream mechanisms (country/latency/target probes, bounded cache, snapshots/history/audit). Exclude Adobe token mint/publish integration from this scope.
- [x] Run `uv run --isolated --no-project --python 3.12 --with requests python -m unittest -q test_quality_engine`.
- [x] Review and commit this isolated component.

## Task 2: Wire rules, inventory, jobs and qualified exports

Files: quality_rules.py, web_app.py, test_quality_api.py, .dockerignore, config.local.json.example.
- [x] Test API authentication/CSRF, profile validation/versioning, preserved secrets/mail config, export compatibility, all 100 entries, no mutation on dry-run, no network on inventory reads, serial background checking and error cleanup.
- [x] Add neutral defaults and validate rules strictly; unknown profiles and invalid country codes/URLs/numbers return 400 before writes. Guard configuration writes with a shared lock. Cache fingerprint makes rule edits invalidate prior observations.
- [x] Build inventory only from currently live proxies; exclude stale accounts and unrelated cached results. Background checks use captured settings/data-dir, bounded batch size, release running flag on every exit, produce snapshots and redacted audit.
- [x] Run targeted tests and baseline 99-test suite in Python 3.12. New modules explicitly allowed in Docker build.

## Task 3: Expose rules and inventory in current dashboard

Files: templates/index.html, static/quality.js, static/app.js, static/app.css, tests/quality-ui.test.cjs, README.md, deploy/PUBLIC_VPS.md.
- [x] Add nav items/views without removing existing IDs or export selectors. Rules view offers profile selection/save/activate, quality enable, latency/countries/concurrency/cache/optional target settings and small dry-run input. Inventory shows untested/pass/fail distinctly, probe progress, country/latency/endpoint table, history, audit and copy qualified subscription.
- [x] Use existing same-origin API/CSRF/error UX; refresh visible inventory every existing four-second dashboard interval. Escape displayed values. Do not display endpoint credentials. Rejecting a check must restore disabled buttons.
- [x] Test JS behavior and desktop/mobile rendering with fixture data, without production registrations.
- [x] Document upstream provenance, intentionally different defaults, qualified-vs-raw exports and deployment compatibility.

## Task 4: Review, merge and deploy

- [x] Independent review of baseline..HEAD plus new tests; resolve important findings.
- [x] Run Python, JS and image build checks; sync tested changes to main without discarding edits.
- [x] Check production active-task state before a restart. Backup current code/config/data, use a new immutable image tag, preserve mounts/certs/tokens and existing Resin subscription.
- [x] Verify public login, CSRF, rules/inventory endpoints, UI assets, old exports and service health. Only read/dry-run a small probe sample; don't change production rules or create accounts. Retain rollback image and backup.
