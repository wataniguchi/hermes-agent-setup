# Hermes Agent Setup — Mac Studio M4 Max (128GB)

Adds [Hermes Agent](https://github.com/NousResearch/hermes-agent) as a second client on this host, alongside [local-agent-webui](https://github.com/wataniguchi/local-agent-webui) (Open WebUI + Open Terminal). Provides autonomous/background multi-agent work and Discord-based access without disrupting the existing Open WebUI setup.

**Assumes:** `local-agent-webui` is already installed and running on this host, with Open WebUI's backend pointed at MLX (`mlx_lm.server`) rather than Ollama.

## How it fits together

```
mlx_lm.server (two separate processes — see note below)
   ├─ :8081 gemma-4-26b-a4b-it-4bit  (primary reasoning)
   │      ├─ open-webui   → existing browser chat + open-terminal sandbox
   │      └─ Hermes       → CLI / gateway / Discord bot, multi-agent, persistent memory
   └─ :8082 gemma-4-e4b-it-4bit      (fast sub-agent, Hermes only)
```

**Why two processes, not one:** `mlx_lm.server` hosts exactly one model per process — unlike Ollama, it doesn't multi-host distinct models with on-demand swapping. Primary and fast sub-agent run as two independent `mlx_lm.server` instances on different ports. Open WebUI and Hermes both point at the primary on `:8081`; only Hermes uses the fast model on `:8082`, for delegation.

**Model policy:** all models here (Gemma) are from a US lab (Google DeepMind), chosen to avoid Chinese-origin open-weight models (Qwen, DeepSeek, Kimi, GLM) per project preference.

**Tool-calling requirement:** `mlx-lm` must be version 0.31.3 or later. Earlier versions fail tool calls outright on `mlx-community/gemma-4-26b-a4b-it-4bit` ([mlx-lm#1125](https://github.com/ml-explore/mlx-lm/issues/1125)), and Hermes depends heavily on reliable tool-calling (`delegate_task`, `memory`, `execute_code`, MCP tools).

## Repo layout

```
hermes-agent-setup/
├── README.md
├── config/
│   ├── config.yaml            # Hermes model + delegation + discord config
│   ├── .env.example           # curated subset — real keys; Discord keys are written by `hermes gateway setup`, not hand-typed
│   ├── cli-config.yaml.dist   # Hermes installer's default config, for reference
│   └── dot_env.dist           # Hermes installer's default .env template, for reference
├── launchagent/
│   ├── com.mlx-primary.plist        # auto-start primary mlx_lm.server (:8081)
│   └── com.mlx-fast.plist           # auto-start fast sub-agent mlx_lm.server (:8082)
└── scripts/
    ├── setup-mlx-models.sh     # downloads the MLX models config.yaml expects
    ├── start-mlx-servers.sh    # manual/foreground launch of both servers
    └── verify-setup.sh         # runs the full verification checklist
```

## Setup

### 1. Prerequisites

- `local-agent-webui` stack healthy, Open WebUI's backend already pointed at MLX
- `mlx-lm` installed, version 0.31.3 or later. This host uses `brew install mlx-lm`, so check with:
  ```bash
  brew list --versions mlx-lm
  ```
  Don't check with `python3 -c "import mlx_lm"` — Homebrew's `mlx-lm` formula builds an isolated venv and only exposes the CLI binaries (`mlx_lm.server`, `mlx_lm.generate`, etc.) on `PATH`; the `mlx_lm` Python module itself isn't importable from your system/user `python3`, even though the CLI works fine. (If you'd instead installed via `pip install mlx-lm`, the Python import check would be the right one — but that's not this setup.)
- Git installed (only manual dependency for the Hermes installer)
- Know your available headroom: 128GB total, minus whatever Docker containers (Open WebUI, Open Terminal) already reserve — the two MLX models here are light (~18GB + a few GB), so this is generous

### 2. Install Hermes Agent

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
hermes postinstall   # installs Playwright — required for browser-based skills
hermes --version
```

Everything Hermes persists lives in `~/.hermes/` (`config.yaml`, `.env`, `SOUL.md`, `memories/`, `skills/`) — untouched by future `hermes` reinstalls/updates.

CLI-only install is what the rest of this guide assumes. If you'd rather use the native macOS app instead of/alongside the terminal, see §10 (Hermes Desktop) — it shares this same `~/.hermes/` state, so nothing below changes either way.

### 3. Download the models and start the servers

```bash
./scripts/setup-mlx-models.sh
./scripts/start-mlx-servers.sh
```

No context-length tagging step is needed here, unlike an Ollama-based setup — `mlx_lm.server` takes context length as a runtime concern, not a per-model tag. `model.context_length` in `config.yaml` is auto-detected from the provider and should be left unset; only set it manually if auto-detection turns out wrong for a given endpoint.

`setup-mlx-models.sh` also checks that your `mlx-lm` version is 0.31.3+, per the tool-calling requirement above. `start-mlx-servers.sh` launches both as background processes and waits for both `/v1/models` endpoints to respond before returning — useful for a one-off manual start; for auto-start at login, use the LaunchAgents in step 8 instead.

### 4. Install the config files

```bash
cp config/config.yaml ~/.hermes/config.yaml
cp config/.env.example ~/.hermes/.env
```

Discord bot auth (`DISCORD_BOT_TOKEN`/`DISCORD_ALLOWED_USERS`) is written automatically by `hermes gateway setup`, not hand-typed into `~/.hermes/.env` (see §7). Fallback provider config uses the key `fallback_model:` (singular) directly in `config.yaml`, not `.env` — see the commented-out block at the bottom of `config/config.yaml` for the full shape and supported providers.

### 5. Concurrency

`mlx_lm.server` (0.31+) handles concurrent requests to a single model through **continuous batching** — a `BatchGenerator` that processes multiple in-flight requests together rather than queuing them one at a time. This means `delegation.max_concurrent_children: 3` (below) genuinely gets 3-way overlap on the fast sub-agent model, not silent serialization — there's no MLX equivalent of `OLLAMA_NUM_PARALLEL` to configure separately.

To check this on your own hardware/model versions, run a sequential vs. concurrent comparison:

```bash
#!/usr/bin/env bash
# concurrency-test.sh — adjust PORT/model to test either server
PORT=8082
URL="http://127.0.0.1:${PORT}/v1/chat/completions"
PROMPT='{"model":"mlx-community/gemma-4-e4b-it-4bit","messages":[{"role":"user","content":"Count slowly from 1 to 50, one number per line."}],"max_tokens":300}'

echo "== Sequential baseline =="
time ( for i in 1 2 3; do curl -s -o /dev/null -w "req $i: %{time_total}s\n" $URL -H "Content-Type: application/json" -d "$PROMPT"; done )

echo; echo "== Concurrent =="
time ( for i in 1 2 3; do curl -s -o /dev/null -w "req $i: %{time_total}s\n" $URL -H "Content-Type: application/json" -d "$PROMPT" & done; wait )
```

If `mlx_lm.server` is silently serializing requests instead of batching them, the concurrent block's total wall-clock time will look about the same as the sequential block's — each request should instead run somewhat slower individually (less GPU throughput per request when sharing a batch) while the batch as a whole finishes well before 3 sequential calls would.

**One trade-off worth knowing about:** the two `mlx_lm.server` processes (primary `:8081`, fast `:8082`) are logically isolated but both compete for the same M4 Max GPU/unified-memory bandwidth at the OS level. Heavy concurrent load on *both* servers simultaneously (e.g. Open WebUI hammering the primary while Hermes runs 3 delegated subagents on the fast model) isn't the same as either one benchmarked alone — test both together if that becomes a real usage pattern.

### 6. Delegation — routing subagents to the fast model

`delegation:` lets `mlx-primary` *dynamically* spawn concurrent subagents mid-task via the `delegate_task` tool — e.g. "research these three things in parallel." This only fires when the primary decides a task benefits from it. Without an explicit `delegation:` block, subagents default to the parent's own model rather than the fast one.

`max_concurrent_children` defaults to 3, with no hard ceiling — nested delegation (subagents that can themselves delegate further) is off by default and needs `delegation.max_spawn_depth` raised explicitly, since each extra level multiplies concurrent load and cost.

To verify subagents are actually landing on the fast model rather than silently inheriting the primary: run a delegation prompt in an interactive `hermes chat` session (not `-q` — a backgrounded `delegate_task` orphans if the CLI process exits before it finishes) while tailing both logs:
```bash
tail -f ~/Library/Logs/mlx-primary.log ~/Library/Logs/mlx-fast.log
```
A request landing in `mlx-fast.log` around the time the delegation fires confirms it's working.

### 7. Discord gateway

1. [Discord Developer Portal](https://discord.com/developers/applications) → New Application
2. **Bot** tab → enable **all three privileged intents**: **Presence Intent**, **Server Members Intent**, and **Message Content Intent**. Discord rejects the gateway connection outright (`discord.errors.PrivilegedIntentsRequired`) if any intent the adapter requests isn't enabled. Scroll down and confirm the page actually saved — it doesn't always auto-save on toggle.
3. **Reset Token**, copy it somewhere safe
4. Generate an invite URL: scopes `bot` + `applications.commands`; permissions: View Channel, Send Messages, Read Message History, Send Messages in Threads, Add Reactions. Invite to your server.
5. Run the setup command and follow its prompts (paste the token when asked):
   ```bash
   hermes gateway setup
   ```
   This writes `DISCORD_BOT_TOKEN` and `DISCORD_ALLOWED_USERS` into `~/.hermes/.env` for you.
6. Restart the gateway:
   ```bash
   hermes gateway restart
   hermes gateway status
   ```

**You must @mention the bot for it to respond** — `discord.require_mention: true` is the default (see `config.yaml`'s `discord:` block). A plain message with no mention is silently ignored; this is expected behavior, not a bug. Test with `@Hermes Agent hello` in a private channel first. If you'd rather not @mention every time in a specific channel, set `discord.free_response_channels` to that channel's ID.

Green/online bot status + a reply to a direct mention confirms the full chain (gateway → provider → Discord) is working. If the bot connects but never replies, check the mention requirement before anything else — it's the most common cause and easy to overlook.

### 8. Auto-start at login

Hermes manages its own launchd plist for the gateway — `hermes gateway setup`/`start` installs `~/Library/LaunchAgents/ai.hermes.gateway.plist` automatically. The following two MLX server plists are what this repo provides.

```bash
# edit both MLX plists first — replace YOUR_USERNAME in each
for f in com.mlx-primary.plist com.mlx-fast.plist; do
  cp "launchagent/$f" ~/Library/LaunchAgents/
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/"$f"
done
```

The gateway itself is managed separately, via Hermes's own commands:
```bash
hermes gateway start     # installs + starts its own launchd service if not already done
hermes gateway status    # confirm supervision status
```

If you used `start-mlx-servers.sh` (step 3) to test manually first, stop those unmanaged foreground/background processes before bootstrapping the MLX LaunchAgents, to avoid two processes fighting over the same port. Since they aren't under launchd yet, plain `pkill` is correct here:
```bash
pkill -f 'mlx_lm.server'
```

**Once a server is running under a LaunchAgent, don't use `pkill`/`kill` to stop it** — `KeepAlive: true` means launchd immediately respawns anything you kill this way, which just looks like the kill silently failed. Use `launchctl bootout` instead, which properly unloads it from supervision first:
```bash
launchctl bootout gui/$(id -u)/com.mlx-fast
launchctl bootout gui/$(id -u)/com.mlx-primary
```
To restart after a `bootout`, re-bootstrap:
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mlx-fast.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mlx-primary.plist
```

### 9. Verify

```bash
./scripts/verify-setup.sh
```

Runs through: both `mlx_lm.server` health checks, a live tool-calling sanity check against the primary model, Hermes CLI response, gateway status, and LaunchAgent presence. Confirm the real Discord reply and Open WebUI's continued normal operation manually.

### 10. Optional: Hermes Desktop as an additional frontend

Nous Research also ships a native macOS app ("Hermes Desktop") — not covered by this repo's setup, and **nothing here needs to change to use it**. It's a fully separate native app that reads the same `~/.hermes/` state (config, memory, skills, sessions) this repo already sets up, so it doesn't touch Open WebUI's existing direct connection to the MLX primary model at all.

- **No config changes required** — it shares `config.yaml`/`.env` automatically once installed; delegation and memory are already available the moment you open it.
- **No `API_SERVER_ENABLED` needed** — that's only for HTTP-based clients like Open WebUI; Desktop talks to the local runtime directly.
- **Download**: from the official Hermes Desktop site (`.dmg` installer), not via Homebrew or `install.sh`.
- **Status**: public preview as of this writing (shipped June 2026) — treat with a bit more caution than the CLI until it matures.
- Adds: a native chat window with streaming tool output, a file browser pane, side-by-side previews of web pages/files/tool output, and voice input.

### 11. Subagent filesystem and shell access

By default, Hermes's Docker terminal backend doesn't expose any host filesystem to subagents — everything is opt-in via `terminal.docker_volumes`. This is what makes it possible to give different directories different trust levels:

```yaml
terminal:
  backend: docker
  docker_volumes:
    - "/path/to/host/docs-collection-1:/corpus/collection-1:ro"
    - "/path/to/host/docs-collection-2:/corpus/collection-2:ro"
    - "/path/to/host/hermes-deliverables:/workspace"
  docker_run_as_host_user: true
  container_persistent: true
```

Fill in real host paths before use — the ones in `config/config.yaml` are placeholders.

**How the enforcement actually works:** every terminal, file, and execute call — from the primary agent or any `delegate_task` subagent — routes through this one Docker container. The read-only vs. read-write split is enforced by the mount itself (`:ro` vs. no suffix), at the kernel level, not by which `toolsets` a given delegation call was given. A subagent can't write to a `:ro`-mounted path no matter what it attempts.

- **Read-only document directories** (`/corpus/*`): mount each source directory separately with a `:ro` suffix. Don't try to unify scattered host directories with symlinks first — Docker bind-mounts don't follow a symlink to a target outside the mounted directory, so a symlink tree mounted alone will show up broken inside the container. Instead, mount each real source directory individually, choosing container-side paths that already look unified (`/corpus/collection-1`, `/corpus/collection-2`, etc.).
- **Deliverables directory** (`/workspace`): a plain read-write host bind mount. Persistence across sessions, projects, and chats is automatic and doesn't depend on `container_persistent` — that flag only affects in-container state (installed packages, etc.), not this bind-mounted data, which lives on the host disk regardless of container lifecycle.
- **`docker_run_as_host_user: true`**: without this, files written to `/workspace` come out root-owned on the host. Only works with the default `docker_image` (or plain Debian/Ubuntu-based images) — not with Hermes's own bundled image, which needs to start as root internally.

Leaf subagents (the kind `delegate_task` spawns by default) can't call Hermes's `memory` tool directly. The natural pattern for turning read documents into persistent knowledge: have the subagent read and summarize, return the summary to the primary, and let the primary decide what's worth writing to memory — this is also what keeps large raw documents out of the main context window in the first place.

## Persistent storage layout

| What | Where | Persists across updates? |
|---|---|---|
| Hermes config/env | `~/.hermes/config.yaml`, `~/.hermes/.env` | Yes |
| Cross-session memory | `~/.hermes/memories/` | Yes |
| Self-improving skills | `~/.hermes/skills/` | Yes |
| `SOUL.md` | `~/.hermes/SOUL.md` | Yes |
| Model weights | `~/.cache/huggingface/hub/` (MLX models download via `huggingface_hub`) | Yes — independent of Hermes |
| Open WebUI chat history | Docker named volume `open-webui-data` | Yes — untouched by Hermes |
| Open Terminal sandbox state | Not persisted by design | Ephemeral, as intended |

For multi-agent instances to *share* memory/context, point Hermes at a pluggable memory provider backed by a local vector DB (Chroma/Qdrant) instead of the default SQLite store. Not necessary for a single-user setup.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'mlx_lm'` despite `mlx_lm.server` running fine | `mlx-lm` installed via `brew install mlx-lm` — CLI binaries are on `PATH`, but the Python module lives in Homebrew's private venv, not importable from system/user `python3` | Use the CLI binaries directly (`mlx_lm.generate`, `mlx_lm.manage`, etc.), which `scripts/setup-mlx-models.sh` already does |
| `ValueError: No function provided` from `mlx_lm.server` on any tool call | `mlx-lm` version 0.31.2 or earlier | `brew upgrade mlx-lm` (or `pip install -U mlx-lm` if pip-installed), confirm 0.31.3+, restart both servers |
| Hermes refuses to initialize, context error | `model.context_length` should be auto-detected, not set | Leave `context_length` unset in `config.yaml`; only set it manually if auto-detection is wrong for this endpoint |
| Discord bot fails to connect: `discord.errors.PrivilegedIntentsRequired` in `gateway.error.log` | One or more of the three privileged intents (Presence, Server Members, Message Content) isn't enabled in the Developer Portal | Enable all three in the Bot tab, confirm it saved, then `hermes gateway restart` |
| Discord bot online and connected, but never replies to messages | `discord.require_mention: true` is the default — plain messages without an @mention are silently ignored, by design | @mention the bot explicitly, or set `discord.free_response_channels` for a channel where you don't want to |
| Port 8081 or 8082 not responding | `mlx_lm.server` process not running, or LaunchAgent not loaded | Check `~/Library/Logs/mlx-primary.log` / `mlx-fast.log`; `launchctl list \| grep mlx` to confirm the LaunchAgents are loaded |
| Two `mlx_lm.server` processes both trying to bind the same port (`Address already in use`, possibly with a repeated crash-loop and climbing `runs` count in `launchctl print`) | An unmanaged process from `start-mlx-servers.sh` is still holding the port while a LaunchAgent is also trying to bind it | `launchctl bootout gui/$(id -u)/com.mlx-fast` (or `com.mlx-primary`) to stop the managed one cleanly, `pkill -f 'mlx_lm.server'` to clear any remaining unmanaged strays, confirm nothing holds the port with `lsof -i :8082`, then re-`bootstrap`. Plain `pkill`/`kill` alone won't work once a LaunchAgent has taken it over — `KeepAlive` just respawns it immediately |
| Hermes tool calls fail / malformed JSON (and `mlx-lm` is already 0.31.3+) | Model too small or not tool-calling-tuned | Confirm you're on `gemma-4-26b-a4b-it-4bit` or `gemma-4-e4b-it-4bit`, not an unrelated small model |
| Gateway doesn't survive reboot | LaunchAgent not loaded, or the MLX servers weren't ready yet at login | Confirm `launchctl bootstrap` succeeded for both MLX plists and `hermes gateway status` shows the gateway supervised |
| Subagents use the primary model instead of the fast one | `delegation:` isn't copied to `~/.hermes/config.yaml`, or `mlx-fast`'s server (`:8082`) isn't running | Check the block is present in `~/.hermes/config.yaml` and `curl http://127.0.0.1:8082/v1/models` responds |
| Subagents seem slower than expected under 3-way concurrency | Expected — continuous batching trades some per-request latency for overall throughput (§5) | Not a bug; re-run `concurrency-test.sh` if the slowdown seems disproportionate |
| Subagent reports a source document path doesn't exist, but it's clearly on the host | Tried to unify scattered directories with symlinks before mounting — Docker bind-mounts don't follow a symlink to a target outside the mounted directory | Mount each real source directory individually in `terminal.docker_volumes` (§11), not a directory of symlinks pointing elsewhere |

## Security notes

- `.env` is gitignored (see below) — never commit it. `.env.example` in this repo has placeholders only.
- If you later expose the Discord bot beyond yourself (teammates/community), create a dedicated Hermes profile for it rather than sharing your personal CLI profile — isolates its skills, memory, and browser sessions.
- If you configure a cloud fallback via `config.yaml`'s `fallback_model:` block (commented out by default — see the bottom of `config/config.yaml`), traffic can leave the machine when local models fail repeatedly — set spend limits on that provider's API key if cost matters. Not currently enabled in this repo's `config.yaml`.

## `.gitignore` (add to repo root)

```
.env
*.log
```
