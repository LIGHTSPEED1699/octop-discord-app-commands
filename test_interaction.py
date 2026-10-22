#!/usr/bin/env python3
"""Test the patched Discord adapter's interaction -> slash-text translation.

Exercises the real DiscordChannel class from the installed package with a fake
discord.py interaction object, and checks what the adapter would enqueue.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3
import sys
import types
from pathlib import Path

sys.path.insert(0, "/home/hongbin/.octop/venv/lib/python3.12/site-packages")

from harness_gateway.channels.discord import DiscordChannel, DiscordConfig  # noqa: E402

DB = Path("/media/intel1TB1/octop/octop.db")


def real_config() -> DiscordConfig:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute(
        "SELECT config_json FROM channels WHERE kind='discord' AND enabled=1 LIMIT 1"
    ).fetchone()
    con.close()
    cfg = json.loads(row[0])
    cfg.pop("bot_token", None)
    return DiscordConfig.from_dict(cfg)


class FakeOption:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


class FakeUser:
    def __init__(self, uid: str) -> None:
        self.id = uid
        self.display_name = "Spot"


class FakeChannel:
    def __init__(self, cid: str, name: str, parent_id: str | None = None) -> None:
        self.id = cid
        self.name = name
        self.parent_id = parent_id


class FakeResponse:
    def __init__(self) -> None:
        self.deferred = False
        self.ephemeral = None

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.deferred = True
        self.ephemeral = ephemeral


class FakeInteraction:
    def __init__(self, *, name, options, channel_id, user_id, guild_id=None, channel=None):
        import discord

        self.type = discord.InteractionType.application_command
        self.data = {"name": name, "options": options}
        self.channel_id = channel_id
        self.user = FakeUser(user_id)
        self.guild_id = guild_id
        self.channel = channel
        self.id = "1234567890"
        self.created_at = dt.datetime.now(dt.timezone.utc)
        self.response = FakeResponse()


class StubProcessor:
    """MessageProcessor stand-in: the adapter only stores it."""

    async def process(self, *args, **kwargs):  # pragma: no cover
        return None


def make_channel(cfg: DiscordConfig) -> DiscordChannel:
    ch = DiscordChannel(StubProcessor(), config=cfg, channel_id="discord", tenant_id="t")
    captured: list = []
    ch._enqueue_callback = captured.append  # type: ignore[attr-defined]
    return ch, captured


async def main() -> int:
    cfg = real_config()
    allowed_user = cfg.allowed_user_ids[0]
    failures: list[str] = []

    # 1. DM from an allowed user, with a model option.
    ch, captured = make_channel(cfg)
    await ch.on_interaction(
        FakeInteraction(
            name="model",
            options=[FakeOption("model", "ollama-cloud/minimax-m3.1-flash")],
            channel_id="999",
            user_id=allowed_user,
        )
    )
    if len(captured) != 1:
        failures.append(f"DM: expected 1 enqueued message, got {len(captured)}")
    else:
        msg = captured[0]
        text = msg.content[0].text
        if text != "/model ollama-cloud/minimax-m3.1-flash":
            failures.append(f"DM: wrong text {text!r}")
        if msg.metadata.get("bot_mentioned") is not True:
            failures.append("DM: bot_mentioned not set (group gate would drop it)")
        if msg.metadata.get("chat_type") != "dm":
            failures.append("DM: wrong chat_type")
        if msg.channel_subject.subject_id != allowed_user:
            failures.append("DM: subject_id is not the sender (thread key would differ)")
        print(f"DM ok  -> text={text!r} subject={msg.channel_subject.subject_id}")

    # 2. DM from a user that is not on the allowlist must be dropped.
    ch, captured = make_channel(cfg)
    await ch.on_interaction(
        FakeInteraction(
            name="model",
            options=[FakeOption("model", "ollama-cloud/minimax-m3.1-flash")],
            channel_id="999",
            user_id="123456789012345678",
        )
    )
    if captured:
        failures.append("DM from a non-allowlisted user was NOT dropped")
    else:
        print("DM deny ok -> non-allowlisted user dropped")

    # 3. Guild channel command, and the thread binding must match the text path.
    ch, captured = make_channel(cfg)
    await ch.on_interaction(
        FakeInteraction(
            name="status",
            options=[],
            channel_id="1478964563528388761",
            user_id=allowed_user,
            guild_id="1478964562740117625",
            channel=FakeChannel("1478964563528388761", "general"),
        )
    )
    if len(captured) != 1:
        failures.append(f"guild: expected 1 enqueued message, got {len(captured)}")
    else:
        msg = captured[0]
        if msg.content[0].text != "/status":
            failures.append(f"guild: wrong text {msg.content[0].text!r}")
        if msg.metadata.get("guild_id") != "1478964562740117625":
            failures.append("guild: guild_id missing")
        if msg.channel_subject.subject_id != "1478964563528388761":
            failures.append("guild: subject_id is not the channel id")
        print(f"guild ok -> text={msg.content[0].text!r} subject={msg.channel_subject.subject_id}")

    # 4. The interaction must be acknowledged inside Discord's 3s window.
    ch, captured = make_channel(cfg)
    it = FakeInteraction(
        name="model", options=[], channel_id="999", user_id=allowed_user
    )
    await ch.on_interaction(it)
    if not it.response.deferred:
        failures.append("interaction was not acknowledged (defer missing)")
    else:
        print("ack ok -> interaction deferred, ephemeral=%s" % it.response.ephemeral)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nall interaction-translation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
