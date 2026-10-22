# Octop → Discord application commands

Makes Octop's slash commands appear in Discord's "/" autocomplete menu and
execute when picked from it.

## Why a patch is needed

Octop's slash commands are **text-level**: the gateway intercepts any message
starting with "/" (`octop/infra/gateway/slash/parser.py`) and routes it to the
`SlashDispatcher`. Discord's "/" menu is populated from Discord's **application
command registry** (`PUT /applications/{app_id}/commands`), which Octop never
touched — the live list was empty, and the adapter had no `on_interaction`
handler, so a registered command would have been delivered as an interaction
and silently dropped.

The patch therefore does two things:

1. `discord_interaction.patch` — adds `on_interaction` to
   `harness_gateway/channels/discord.py`. It translates an application-command
   interaction back into its text form (`/model ollama-cloud/minimax-m3.1-flash`)
   and enqueues it, so there is exactly one execution path: ctx building,
   thread binding, dispatcher, reply. Authorization follows the same rules as
   `on_message` (DM allowlist, allowed channels).
2. `register_discord_commands.py` — builds the Discord payload from the **live**
   catalog (`octop.infra.gateway.slash.catalog`, `visible_in("im")` only) and
   PUTs it globally and per guild. `/model` gets CHOICES built from the enabled
   providers/models in `octop.db`, so the dropdown only offers valid
   `provider/model` refs.

## Files

| path | role |
|---|---|
| `discord_interaction.patch` | unified diff against `harness_gateway/channels/discord.py` (marker: `octop-patch: discord-interaction-support v2`) |
| `install_adapter_patch.py` | idempotent, version-aware installer; restores the pristine file from `backups/` before re-applying |
| `register_discord_commands.py` | registers the command list with Discord (global + per guild) |
| `test_interaction.py` | offline test of the interaction → text translation, incl. the DM allowlist |
| `backups/discord.py.orig-*` | pristine copies of the upstream adapter |

## systemd wiring

`~/.config/systemd/user/octop.service.d/20-discord-app-commands.conf`

```ini
[Service]
ExecStartPre=-/home/hongbin/.octop/venv/bin/python /media/intel1TB1/octop-patches/install_adapter_patch.py
ExecStartPost=-/home/hongbin/.octop/venv/bin/python /media/intel1TB1/octop-patches/register_discord_commands.py
```

Both are best-effort (`-` prefix): a failure must not take the service down.
`ExecStartPre` re-applies the patch after an `octop update` replaces
site-packages, and `ExecStartPost` re-registers the list (so a changed model
catalog or a new command is picked up on the next start).

## Manual operations

```bash
# re-register now (safe to run any time)
cd /home/hongbin/.octop && ./venv/bin/python /media/intel1TB1/octop-patches/register_discord_commands.py

# re-apply the adapter patch
/media/intel1TB1/octop-patches/install_adapter_patch.py

# run the offline test
cd /home/hongbin/.octop && ./venv/bin/python /media/intel1TB1/octop-patches/test_interaction.py
```

## Notes

- Discord's **global** command list propagates to clients within ~1 hour; the
  **guild** list is instant but only applies inside `OpenClawServer`
  (1478964562740117625). DM usage depends on the global list.
- A model ref is `provider/model` (slash). The catalog's own help text says
  `provider:model` — that form does not resolve (`resolve_model_ref` splits on
  `/` only).
- The interaction is acknowledged with an ephemeral defer (Discord's 3 s
  window); the command's result arrives as a normal channel message.
- The bot must not have an "interactions endpoint URL" configured, otherwise
  discord.py refuses to receive interactions.
