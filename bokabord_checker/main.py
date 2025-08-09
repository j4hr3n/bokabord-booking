import argparse
import json
import os
import re
import sys
import time
import calendar
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import yaml
from email.header import Header


TIME_RE = re.compile(r"^\d{2}:\d{2}$")


@dataclass
class Config:
    endpoint_url: str
    mealid: str
    payload_template: Dict[str, Any]
    date_selection: Dict[str, Any]
    party_size: int
    time_filters: Dict[str, Any]
    ntfy: Dict[str, Any]
    request: Dict[str, Any]
    debug: bool = False


def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Config(
        endpoint_url=raw["endpoint_url"],
        mealid=str(raw["mealid"]),
        payload_template=raw["payload_template"],
        date_selection=raw["date_selection"],
        party_size=int(raw["party_size"]),
        time_filters=raw["time_filters"],
        ntfy=raw["ntfy"],
        request=raw["request"],
        debug=bool(raw.get("debug", False)),
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check Bokabord availability for Punk Royale and notify via ntfy")
    p.add_argument("--config", default=os.environ.get("CONFIG", "config.yaml"), help="Path to config.yaml")
    # Accept strings for env-backed defaults; convert later if non-empty
    p.add_argument("--party", default=os.environ.get("PARTY_SIZE"), help="Override party size")
    p.add_argument("--dates", default=os.environ.get("DATES"), help="Comma-separated YYYY-MM-DD list to check")
    p.add_argument("--month", default=os.environ.get("MONTH"), help="Month (1-12) if not using --dates")
    p.add_argument("--year", default=os.environ.get("YEAR"), help="Year if not using --dates")
    p.add_argument("--dow", default=os.environ.get("DOW", "Friday"), help="Day of week to include (e.g., Friday)")
    p.add_argument("--time-window", default=os.environ.get("TIME_WINDOW"), help="HH:MM-HH:MM inclusive window")
    p.add_argument("--allowlist", default=os.environ.get("ALLOWLIST"), help="Comma-separated explicit times to allow")
    p.add_argument("--ntfy-topic", default=os.environ.get("NTFY_TOPIC"), help="Override ntfy topic")
    p.add_argument("--dry-run", action="store_true", help="Do not send ntfy notification")
    p.add_argument("--debug", action="store_true", help="Enable extra logging")
    return p.parse_args()


def compute_fridays(year: int, month: int, day_name: str) -> List[str]:
    target_weekday = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].index(day_name)
    _, num_days = calendar.monthrange(year, month)
    dates: List[str] = []
    for d in range(1, num_days + 1):
        dt = date(year, month, d)
        if dt.weekday() == target_weekday:
            dates.append(dt.isoformat())
    return dates


def resolve_dates(cfg: Config, args: argparse.Namespace) -> List[str]:
    if args.dates:
        return [d.strip() for d in args.dates.split(",") if d.strip()]

    sel = cfg.date_selection
    year = int(args.year) if args.year else (int(sel.get("year")) if sel.get("year") else datetime.now().year)
    month = int(args.month) if args.month else int(sel.get("month", 11))
    dow = args.dow or sel.get("day_of_week", "Friday")

    if sel.get("specific_dates"):
        return list(sel["specific_dates"])  # assumes already in YYYY-MM-DD

    return compute_fridays(year, month, dow)


def within_time_window(t: str, earliest: Optional[str], latest: Optional[str]) -> bool:
    if not TIME_RE.match(t):
        return False
    if earliest and t < earliest:
        return False
    if latest and t > latest:
        return False
    return True


def extract_times_from_response(data: Any) -> List[str]:
    times: List[str] = []

    def walk(obj: Any):
        if isinstance(obj, dict):
            # common shapes: {"times": [{"time": "19:00", "available": true}]}
            if "times" in obj and isinstance(obj["times"], list):
                for item in obj["times"]:
                    if isinstance(item, dict):
                        t = item.get("time") or item.get("slot") or item.get("label")
                        if isinstance(t, str) and TIME_RE.match(t):
                            available = item.get("available")
                            if available is None or bool(available):
                                times.append(t)
                    elif isinstance(item, str) and TIME_RE.match(item):
                        times.append(item)
            # generic scan
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, str):
            if TIME_RE.match(obj):
                times.append(obj)

    walk(data)
    # de-duplicate & sort
    return sorted(set(times))


def build_payload(base: Dict[str, Any], date_str: str, amount: int, mealid: str) -> Dict[str, Any]:
    payload = dict(base)
    payload["date"] = date_str
    payload["amount"] = amount
    payload["mealid"] = str(mealid)
    return payload


def post_json_with_retries(session: requests.Session, url: str, json_body: Dict[str, Any], headers: Dict[str, str], retries: int, timeout: float, debug: bool) -> Tuple[Optional[requests.Response], Optional[Exception]]:
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 2):
        try:
            resp = session.post(url, json=json_body, headers=headers, timeout=timeout)
            return resp, None
        except Exception as e:  # requests exceptions
            last_exc = e
            if debug:
                print(f"Attempt {attempt} failed: {e}", file=sys.stderr)
            time.sleep(min(1.5 * attempt, 5.0))
    return None, last_exc


def _rfc2047_if_needed(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return Header(value, "utf-8").encode()


def notify_ntfy(server: str, topic: str, title: str, body: str, priority: Optional[str] = None) -> None:
    url = f"{server.rstrip('/')}/{topic}"
    headers = {}
    if title:
        headers["Title"] = _rfc2047_if_needed(title) or ""
    if priority:
        headers["Priority"] = priority
    requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=15)


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)

    if args.debug:
        cfg.debug = True

    # Overrides
    if args.party:
        cfg.party_size = int(args.party)
    if args.ntfy_topic:
        cfg.ntfy["topic"] = args.ntfy_topic
    if args.time_window:
        earliest, latest = None, None
        parts = str(args.time_window).split("-")
        if len(parts) == 2:
            earliest, latest = parts[0].strip(), parts[1].strip()
            cfg.time_filters["earliest"], cfg.time_filters["latest"] = earliest, latest
    if args.allowlist:
        cfg.time_filters["allowlist"] = [t.strip() for t in str(args.allowlist).split(",") if t.strip()]

    session = requests.Session()
    session.headers.update({
        "User-Agent": cfg.request.get("user_agent", "bokabord-checker/1.0"),
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://app.bokabord.se",
        "Referer": "https://app.bokabord.se/",
    })

    dates = resolve_dates(cfg, args)

    all_matches: Dict[str, List[str]] = {}

    for d in dates:
        payload = build_payload(cfg.payload_template, d, cfg.party_size, cfg.mealid)
        if cfg.debug:
            print(f"Request payload for {d}: {json.dumps(payload, ensure_ascii=False)}")
        resp, err = post_json_with_retries(
            session=session,
            url=cfg.endpoint_url,
            json_body=payload,
            headers={},
            retries=int(cfg.request.get("retries", 1)),
            timeout=float(cfg.request.get("timeout_seconds", 15)),
            debug=cfg.debug,
        )
        if err is not None:
            print(f"Failed to query {d}: {err}", file=sys.stderr)
            continue
        if resp is None:
            print(f"No response for {d}", file=sys.stderr)
            continue

        body_text = resp.text
        if cfg.debug:
            print(f"Response for {d}: {body_text}")

        try:
            data = resp.json()
        except Exception:
            print(f"Non-JSON response for {d}: {body_text}", file=sys.stderr)
            continue

        if not isinstance(data, dict):
            continue

        # Expect success field if present
        if data.get("success") is False:
            # keep going, but log
            err_text = data.get("errors") or data.get("error")
            print(f"API reported failure for {d}: {err_text}", file=sys.stderr)
            # still try to scan times in case shape differs

        times = extract_times_from_response(data)

        # Apply filters
        tf = cfg.time_filters
        allowlist: List[str] = tf.get("allowlist") or []
        earliest = tf.get("earliest")
        latest = tf.get("latest")

        filtered: List[str]
        if allowlist:
            allowed_set = set(allowlist)
            filtered = sorted(t for t in times if t in allowed_set)
        else:
            filtered = sorted(t for t in times if within_time_window(t, earliest, latest))

        if cfg.debug:
            print(f"Times found for {d}: {times}")
            print(f"Times after filter for {d}: {filtered}")

        if filtered:
            all_matches[d] = filtered

        # be polite
        time.sleep(0.5)

    if not all_matches:
        print("No matching availability found.")
        return 0

    # Build message
    lines: List[str] = [
        "Bord funnet:",
        "",
    ]
    for d, slots in sorted(all_matches.items()):
        lines.append(f"- {d}: {', '.join(slots)}")
    message = "\n".join(lines)
    print(message)

    if not args.dry_run:
        notify_ntfy(
            server=cfg.ntfy.get("server", "https://ntfy.sh"),
            topic=cfg.ntfy.get("topic", "j4hr3n"),
            title=cfg.ntfy.get("title", "Bokabord availability"),
            body=message,
            priority=cfg.ntfy.get("priority"),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
