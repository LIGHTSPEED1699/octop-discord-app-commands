#!/usr/bin/env python3
"""Register Octow's IM-visible slash commands as Discord application commands.

Octow's slash commands are text-level (the gateway intercepts messages that
start with "/"). Registering them with Discord is what puts them in the client's
"/" autocomplete menu. The adapter patch (install_adapter_patch.py) is what makes
the resulting interactions execute.

The payload is derived from the live catalog (octop.infra.gateway.slash.catalog)
so it always matches what the dispatcher actually serves.

Idempotent: Discord's PUT replaces the whole list, so running it repeatedly is
safe. Called from ExecStartPost and safe to run by hand.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

DB = Path("/media/intel1TB1/octop/octop.db")
TOKEN_FILE = Path("/media/intel1TB1/octop/discordBot")
API = "https://discord.com/api/v10"

# Argument token -> (option name, option description). Derived from the
# catalog's usage strings; anything not listed here is registered without an
# option (the command takes no argument).
OPTION_HINTS: dict[str, tuple[str, str]] = {
    "pending_id": ("pending_id", "The approval id from /pending"),
    "short_id": ("short_id", "Short id of the thread"),
    "reason": ("reason", "Reason for rejecting"),
    "text": ("text", "New title"),
    "title": ("title", "Title for the new thread"),
}


def log(msg: str) -> None:
    print(f"[register-commands] {msg}", flush=True)


def api(method: str, path: str, token: str, payload: list | None = None) -> object:
    url = f"{API}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bot {token}")
    # Discord's edge rejects requests without a descriptive User-Agent
    # (error 1010) - Python's default urllib UA is blocked.
    req.add_header("User-Agent", "OctopDiscordSync (https://github.com/octop, 1.0)")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode()
    return json.loads(body) if body else None


def bot_token() -> str:
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        row = con.execute(
            "SELECT config_json FROM channels WHERE kind='discord' AND enabled=1 LIMIT 1"
        ).fetchone()
        con.close()
        if row:
            cfg = json.loads(row[0])
            tok = str(cfg.get("bot_token") or "").strip()
            if tok:
                return tok
    except Exception as exc:  # noqa: BLE001
        log(f"db read failed ({exc}); falling back to {TOKEN_FILE}")
    return TOKEN_FILE.read_text().strip()


def enabled_models() -> list[str]:
    refs: list[str] = []
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        for name, models_json in con.execute("SELECT name, models_json FROM providers"):
            try:
                models = json.loads(models_json or "[]")
            except Exception:  # noqa: BLE001
                continue
            for m in models:
                if isinstance(m, dict) and m.get("enabled"):
                    refs.append(f"{name}/{m.get('id')}")
        con.close()
    except Exception as exc:  # noqa: BLE001
        log(f"provider read failed: {exc}")
    return refs


def arg_token(usage: str) -> str | None:
    m = re.search(r"[\[<]([^\]>]+)[\]>]", usage)
    if not m:
        return None
    token = m.group(1).split("|")[0].strip()
    token = token.split(":")[-1].strip()
    return re.sub(r"[^a-z0-9_]+", "_", token.lower()) or None


def build_payload() -> list[dict]:
    from octop.infra.gateway.slash.catalog import CATALOG

    models = enabled_models()
    payload: list[dict] = []
    for spec in CATALOG:
        if not spec.visible_in("im"):
            continue
        description = (spec.description_en or spec.name).strip()[:100] or spec.name
        cmd: dict = {
            "name": spec.name,
            "description": description,
            "type": 1,
            "options": [],
        }
        token = arg_token(spec.usage)
        if token:
            name, desc = OPTION_HINTS.get(token, (token, f"Argument for /{spec.name}"))
            option: dict = {
                "name": name[:32],
                "description": desc[:100],
                "type": 3,  # STRING
                "required": False,
            }
            if spec.name == "model":
                choices = [{"name": m[:100], "value": m[:100]} for m in models]
                choices.append({"name": "reset", "value": "reset"})
                if choices:
                    option["choices"] = choices[:25]
            cmd["options"].append(option)
        payload.append(cmd)
    return payload


def main() -> int:
    try:
        token = bot_token()
    except Exception as exc:  # noqa: BLE001
        log(f"could not read the bot token: {exc}")
        return 1
    if not token:
        log("no bot token found")
        return 1
    try:
        me = api("GET", "/users/@me", token)
    except urllib.error.HTTPError as exc:
        log(f"Discord auth failed: HTTP {exc.code} {exc.read().decode()[:200]}")
        return 1
    except Exception as exc:  # noqa: BLE001
        log(f"Discord API unreachable: {exc}")
        return 1
    app_id = str(me.get("id"))
    log(f"application id {app_id} ({me.get('username')})")

    payload = build_payload()
    log(f"registering {len(payload)} commands: {', '.join(c['name'] for c in payload)}")

    ok = True
    try:
        guilds = api("GET", "/users/@me/guilds", token)
    except Exception as exc:  # noqa: BLE001
        log(f"guild lookup failed: {exc}")
        guilds = []
    for g in guilds or []:
        gid = str(g.get("id"))
        try:
            api("PUT", f"/applications/{app_id}/guilds/{gid}/commands", token, payload)
            log(f"guild {gid} ({g.get('name')}): {len(payload)} commands registered (instant)")
        except Exception as exc:  # noqa: BLE001
            log(f"guild {gid} registration failed: {exc}")
            ok = False
    try:
        api("PUT", f"/applications/{app_id}/commands", token, payload)
        log(f"global: {len(payload)} commands registered (propagates within ~1h)")
    except Exception as exc:  # noqa: BLE001
        log(f"global registration failed: {exc}")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
