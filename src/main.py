import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "teams.json"
STATE_PATH = ROOT / "state" / "sent.json"

API_BASE = "https://v3.football.api-sports.io"
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"

PRE_MATCH_MINUTES = 30
START_GRACE_MINUTES = 60          # don't send "starts now" if we're more than 1h late
STATE_RETENTION_DAYS = 3
LOCAL_TZ = timezone(timedelta(hours=-3))   # Argentina (UTC-3, no DST)


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"Missing env var: {name}", file=sys.stderr)
        sys.exit(1)
    return v


def fetch_fixtures(team_id: int, api_key: str) -> list[dict]:
    r = requests.get(
        f"{API_BASE}/fixtures",
        headers={"x-apisports-key": api_key},
        params={"team": team_id, "next": 5},
        timeout=20,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        print(f"API errors for team {team_id}: {body['errors']}", file=sys.stderr)
    return body.get("response", [])


def send_whatsapp(phone: str, apikey: str, message: str) -> None:
    r = requests.get(
        CALLMEBOT_URL,
        params={"phone": phone, "text": message, "apikey": apikey},
        timeout=30,
    )
    if r.status_code >= 400:
        print(f"CallMeBot error {r.status_code}: {r.text[:200]}", file=sys.stderr)
        r.raise_for_status()
    # CallMeBot rate-limits: be polite
    time.sleep(2)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def prune_state(state: dict, now: datetime) -> dict:
    cutoff = now - timedelta(days=STATE_RETENTION_DAYS)
    pruned = {}
    for fid, entry in state.items():
        try:
            ko = datetime.fromisoformat(entry["kickoff"])
        except (KeyError, ValueError):
            continue
        if ko > cutoff:
            pruned[fid] = entry
    return pruned


def fmt_local(dt_utc: datetime) -> str:
    return dt_utc.astimezone(LOCAL_TZ).strftime("%H:%M")


def build_pre_message(team_emoji: str, league: str, home: str, away: str, kickoff_utc: datetime) -> str:
    return (
        f"{team_emoji} En 30 minutos\n"
        f"⚽ {home} vs {away}\n"
        f"🏆 {league}\n"
        f"🕐 {fmt_local(kickoff_utc)} hs"
    )


def build_start_message(team_emoji: str, league: str, home: str, away: str) -> str:
    return (
        f"{team_emoji} ¡EMPIEZA EL PARTIDO!\n"
        f"🔴 {home} vs {away}\n"
        f"🏆 {league}"
    )


def main() -> int:
    api_key = env("API_FOOTBALL_KEY")
    phone = env("WHATSAPP_PHONE")
    apikey = env("WHATSAPP_APIKEY")

    teams = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["teams"]
    now = datetime.now(timezone.utc)
    state = prune_state(load_state(), now)

    for team in teams:
        try:
            fixtures = fetch_fixtures(team["id"], api_key)
        except requests.RequestException as e:
            print(f"Failed to fetch fixtures for {team['name']}: {e}", file=sys.stderr)
            continue

        for fx in fixtures:
            fixture_id = str(fx["fixture"]["id"])
            kickoff = datetime.fromisoformat(fx["fixture"]["date"].replace("Z", "+00:00"))
            league = fx["league"]["name"]
            home = fx["teams"]["home"]["name"]
            away = fx["teams"]["away"]["name"]

            entry = state.get(fixture_id, {
                "kickoff": kickoff.isoformat(),
                "team": team["name"],
                "match": f"{home} vs {away}",
                "league": league,
                "pre_sent": False,
                "start_sent": False,
            })
            entry["kickoff"] = kickoff.isoformat()  # refresh in case of postponement

            minutes_until = (kickoff - now).total_seconds() / 60.0

            # 30-minute pre-match: send once we're inside the 30-min window and the match hasn't started
            if not entry["pre_sent"] and 0 < minutes_until <= PRE_MATCH_MINUTES:
                msg = build_pre_message(team["emoji"], league, home, away, kickoff)
                try:
                    send_whatsapp(phone, apikey, msg)
                    entry["pre_sent"] = True
                    print(f"[pre] {team['name']}: {home} vs {away} in {minutes_until:.0f} min")
                except requests.RequestException as e:
                    print(f"WhatsApp send failed (pre): {e}", file=sys.stderr)

            # Kickoff: send once kickoff has passed but not too late
            minutes_since = -minutes_until
            if not entry["start_sent"] and 0 <= minutes_since <= START_GRACE_MINUTES:
                msg = build_start_message(team["emoji"], league, home, away)
                try:
                    send_whatsapp(phone, apikey, msg)
                    entry["start_sent"] = True
                    print(f"[start] {team['name']}: {home} vs {away}")
                except requests.RequestException as e:
                    print(f"WhatsApp send failed (start): {e}", file=sys.stderr)

            state[fixture_id] = entry

    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
