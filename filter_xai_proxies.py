# -*- coding: utf-8 -*-
"""
Filter nodeAuto proxy exports for xAI/Grok reachability.

Default behavior:
  - reads the newest node/proxies_*.txt file
  - tests each proxy against accounts.x.ai sign-up
  - writes working proxies to node/working_xai_YYYYmmdd_HHMMSS.txt
"""

import argparse
import concurrent.futures
import os
import sys
import time
from datetime import datetime

try:
    from curl_cffi import requests
except Exception as exc:
    raise SystemExit(
        "Missing dependency curl_cffi. Install with: python -m pip install curl_cffi"
    ) from exc


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NODE_DIR = os.path.join(BASE_DIR, "node")
TEST_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"


def log(message):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def latest_proxy_file():
    if not os.path.isdir(NODE_DIR):
        raise FileNotFoundError(f"node dir not found: {NODE_DIR}")
    files = [
        os.path.join(NODE_DIR, name)
        for name in os.listdir(NODE_DIR)
        if name.startswith("proxies_") and name.endswith(".txt")
    ]
    if not files:
        raise FileNotFoundError(f"no proxies_*.txt found in {NODE_DIR}")
    return max(files, key=os.path.getmtime)


def normalize_proxy(line):
    proxy = line.strip()
    if not proxy or proxy.startswith("#"):
        return ""
    if "://" not in proxy:
        proxy = "http://" + proxy
    return proxy


def load_proxies(path):
    seen = set()
    proxies = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            proxy = normalize_proxy(line)
            if not proxy or proxy in seen:
                continue
            seen.add(proxy)
            proxies.append(proxy)
    return proxies


def check_proxy(proxy, timeout, url):
    started = time.monotonic()
    try:
        resp = requests.get(
            url,
            proxies={"http": proxy, "https": proxy},
            timeout=timeout,
            impersonate="chrome120",
            allow_redirects=False,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if 200 <= resp.status_code < 400:
            return True, proxy, resp.status_code, elapsed_ms, ""
        return False, proxy, resp.status_code, elapsed_ms, f"HTTP {resp.status_code}"
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return False, proxy, 0, elapsed_ms, str(exc).splitlines()[0][:160]


def main():
    parser = argparse.ArgumentParser(description="Filter proxies that can open accounts.x.ai.")
    parser.add_argument("--input", "-i", default="", help="proxy file, default: newest node/proxies_*.txt")
    parser.add_argument("--output", "-o", default="", help="output file, default: node/working_xai_TIMESTAMP.txt")
    parser.add_argument("--url", default=TEST_URL, help=f"test URL, default: {TEST_URL}")
    parser.add_argument("--timeout", type=float, default=15.0, help="per-proxy timeout seconds")
    parser.add_argument("--workers", type=int, default=30, help="concurrent workers")
    parser.add_argument("--limit", type=int, default=0, help="only test first N proxies")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input) if args.input else latest_proxy_file()
    output_path = (
        os.path.abspath(args.output)
        if args.output
        else os.path.join(NODE_DIR, f"working_xai_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    )

    proxies = load_proxies(input_path)
    if args.limit and args.limit > 0:
        proxies = proxies[: args.limit]
    if not proxies:
        raise SystemExit("No proxies to test.")

    workers = max(1, min(args.workers, len(proxies)))
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    log(f"input: {input_path}")
    log(f"output: {output_path}")
    log(f"testing {len(proxies)} proxies, workers={workers}, timeout={args.timeout}s")

    ok_count = 0
    done_count = 0
    with open(output_path, "w", encoding="utf-8", newline="\n") as out:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(check_proxy, proxy, args.timeout, args.url)
                for proxy in proxies
            ]
            for future in concurrent.futures.as_completed(futures):
                ok, proxy, status, elapsed_ms, err = future.result()
                done_count += 1
                if ok:
                    ok_count += 1
                    out.write(proxy + "\n")
                    out.flush()
                    log(f"[OK] {ok_count}/{done_count}/{len(proxies)} {status} {elapsed_ms}ms {proxy}")
                elif done_count % 25 == 0 or len(proxies) <= 50:
                    log(f"[--] {done_count}/{len(proxies)} {elapsed_ms}ms {err}")

    log(f"done: working={ok_count}, tested={len(proxies)}")
    if ok_count == 0:
        log("no working xAI proxies found")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
