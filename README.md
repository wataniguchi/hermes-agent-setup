# Hermes Agent Setup — Mac Studio M4 Max (128GB)

Adds [Hermes Agent](https://github.com/NousResearch/hermes-agent) as a second client on this host, alongside [local-agent-webui](https://github.com/wataniguchi/local-agent-webui) (Open WebUI + Open Terminal). Provides autonomous/background multi-agent work and Discord-based access without disrupting the existing Open WebUI setup.

**Assumes:** `local-agent-webui` is already installed and running on this host, with Open WebUI's backend pointed at the same Ollama instance this repo configures.

## How it fits together

```
Ollama (single process, serves both models by name)
   ├─ gemma4:26b   (primary reasoning)
   │      ├─ open-webui   → existing browser chat + open-terminal sandbox
   │      └─ Hermes       → CLI / gateway / Discord bot, multi-agent, persistent memory
   └─ gemma4:e4b   (fast sub-agent, Hermes only, via delegation)
```

**One Ollama instance serves both models** — unlike a per-model-process backend, there's no separate port/process per model here; requests specify which model by name, and Ollama handles loading/serving both from one endpoint (`http://localhost:11434`).

**Model policy:** both models (Gemma 4) are from a US lab (Google DeepMind), chosen to avoid Chinese-origin open-weight models (Qwen, DeepSeek, Kimi, GLM) per project preference.

**Version requirement:** Ollama 0.22.0 or later — earlier builds predate the `llama.cpp` Gemma 4 fixes, particularly around tool-calling reliability, which Hermes depends on heavily (`delegate_task`, `memory`, `execute_code`, MCP tools).

## Repo layout

```
hermes-agent-setup/
├── README.md
├── config/
│   ├── config.yaml            # Hermes model + delegation + discord config
│   ├── .env.example           # curated subset — real keys; Discord keys are written by `hermes gateway setup`, not hand-typed
│   ├── cli-config.yaml.dist   # Hermes installer's default config, for reference
│   └── dot_env.dist           # Hermes installer's default .env template, for reference
└── scripts/
    ├── setup-ollama-models.sh   # checks Ollama version, pulls both models
    └── verify-setup.sh          # runs the full verification checklist
```

No custom LaunchAgents ship from this repo — Ollama is managed via `brew services` (its own Homebrew-installed plist), and the Hermes gateway manages its own launchd service (`hermes gateway start`/`setup`). Both are covered in §7 and §8 below.

## Setup

### 1. Prerequisites

- `local-agent-webui` stack healthy, Open WebUI's backend already pointed at this same Ollama instance
- Ollama installed via `brew install ollama`, version 0.22.0 or later — check with `ollama --version`
- Git installed (only manual dependency for the Hermes installer)
- Know your available headroom: 128GB total, minus whatever Docker containers (Open WebUI, Open Terminal) already reserve — `gemma4:26b` runs ~19GB resident, `gemma4:e4b` a few GB, so this is generous even with both loaded at once

### 2. Install Hermes Agent

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
hermes postinstall   # installs Playwright — required for browser-based skills
hermes --version
```

Everything Hermes persists lives in `~/.hermes/` (`config.yaml`, `.env`, `SOUL.md`, `memories/`, `skills/`) — untouched by future `hermes` reinstalls/updates.

CLI-only install is what the rest of this guide assumes. If you'd rather use the native macOS app instead of/alongside the terminal, see §10 (Hermes Desktop) — it shares this same `~/.hermes/` state, so nothing below changes either way.

### 3. Pull the models

```bash
./scripts/setup-ollama-models.sh
```

Pulls `gemma4:26b` (primary) and `gemma4:e4b` (fast sub-agent), and checks your installed Ollama version against the 0.22.0+ requirement. Context window (262144 for the primary) is auto-detected by Ollama — no manual override needed.

### 4. Install the config files

```bash
cp config/config.yaml ~/.hermes/config.yaml
cp config/.env.example ~/.hermes/.env
```

Discord bot auth (`DISCORD_BOT_TOKEN`/`DISCORD_ALLOWED_USERS`) is written automatically by `hermes gateway setup`, not hand-typed into `~/.hermes/.env` (see §7). Fallback provider config uses the key `fallback_model:` (singular) directly in `config.yaml`, not `.env` — see the commented-out block at the bottom of `config/config.yaml` for the full shape and supported providers.

### 5. Residency and concurrency

Since one Ollama instance serves both models, two settings matter for keeping both resident and letting delegation's concurrent subagents actually run in parallel rather than queue:

```bash
OLLAMA_MAX_LOADED_MODELS=2   # keep both gemma4:26b and gemma4:e4b resident at once
OLLAMA_NUM_PARALLEL=3        # match delegation.max_concurrent_children — otherwise Ollama queues the excess
```

Ollama runs as a `brew services` background daemon, launched by `launchd`, not your shell — these variables need to go in the brew-managed plist's `EnvironmentVariables` block, not `~/.zshrc`.

```bash
ls -la ~/Library/LaunchAgents/ | grep ollama
```
Typically `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist`. Edit it:
```xml
<key>EnvironmentVariables</key>
<dict>
    <key>OLLAMA_MAX_LOADED_MODELS</key>
    <string>2</string>
    <key>OLLAMA_NUM_PARALLEL</key>
    <string>3</string>
</dict>
```
If the plist already has an `EnvironmentVariables` dict, add these keys inside the existing one rather than creating a second — a plist can't have two `EnvironmentVariables` keys at the same level.

```bash
brew services restart ollama
```

Verify both models actually stay resident under real concurrent use:
```bash
ollama ps
```

### 6. Delegation — routing subagents to the fast model

`delegation:` lets the primary *dynamically* spawn concurrent subagents mid-task via the `delegate_task` tool — e.g. "research these three things in parallel." This only fires when the primary decides a task benefits from it. Without an explicit `delegation:` block, subagents default to the parent's own model rather than the fast one.

`max_concurrent_children` defaults to 3, with no hard ceiling — nested delegation (subagents that can themselves delegate further) is off by default and needs `delegation.max_spawn_depth` raised explicitly, since each extra level multiplies concurrent load and cost.

To verify subagents are actually landing on the fast model rather than silently inheriting the primary: run a delegation prompt in an interactive `hermes chat` session (not `-q` — a backgrounded `delegate_task` orphans if the CLI process exits before it finishes) while watching:
```bash
ollama ps
```
Both models should show as active/resident during a real delegation.

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

Two services, each managing its own launchd registration — nothing custom needed from this repo:

- **Ollama**: `brew services start ollama` installs and manages `homebrew.mxcl.ollama.plist` automatically. `brew services list` confirms it's running.
- **Hermes gateway**: `hermes gateway setup`/`start` installs and manages `~/Library/LaunchAgents/ai.hermes.gateway.plist` automatically. `hermes gateway status` confirms supervision.

### 9. Verify

```bash
./scripts/verify-setup.sh
```

Runs through: Ollama reachability, both models pulled, a live tool-calling sanity check, Hermes CLI response, gateway status. Confirm the real Discord reply and Open WebUI's continued normal operation manually.

### 10. Optional: Hermes Desktop as an additional frontend

Nous Research also ships a native macOS app ("Hermes Desktop") — not covered by this repo's setup, and **nothing here needs to change to use it**. It's a fully separate native app that reads the same `~/.hermes/` state (config, memory, skills, sessions) this repo already sets up, so it doesn't touch Open WebUI's existing connection to Ollama at all.

- **No config changes required** — it shares `config.yaml`/`.env` automatically once installed; delegation and memory are already available the moment you open it.
- **No `API_SERVER_ENABLED` needed** — that's only for HTTP-based clients like Open WebUI; Desktop talks to the local runtime directly.
- **Download**: from the official Hermes Desktop site (`.dmg` installer), not via Homebrew or `install.sh`.
- **Status**: public preview as of this writing (shipped June 2026) — treat with a bit more caution than the CLI until it matures.
- Adds: a native chat window with streaming tool output, a file browser pane, side-by-side previews of web pages/files/tool output, and voice input.

**Known limitation: Desktop does not reliably apply `terminal.backend: docker`.** With `terminal:` fully and correctly configured in `~/.hermes/config.yaml` (§11), a Desktop session asked to read a Docker-mounted path fell back to running the command directly on the host instead — no container was created, no error was shown to the user, and the model simply reported the path as not found (having actually listed the real host root directory). The same request via `hermes chat -q` and via the Discord gateway both worked correctly and created the sandbox container as expected, confirming the config itself is correct and the gap is specific to Desktop. Nous Research's own issue tracker documents at least one other case of Desktop's Docker-backend launch path diverging from the CLI/gateway's, so this looks like a real product gap rather than a one-off misconfiguration. This limitation is independent of which model backend (Ollama, MLX, anything else) is in use — it's about the Docker sandbox specifically, not model serving.

**Until this is fixed upstream, don't rely on Desktop for anything that needs the sandboxed corpus/deliverables setup in §11 — use `hermes chat` or the Discord gateway instead.** Desktop remains fine for everything that doesn't require the Docker terminal backend.

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

**Verify via CLI or Discord, not Desktop** — see §10's known limitation above.

**How the enforcement actually works:** every terminal, file, and execute call — from the primary agent or any `delegate_task` subagent — routes through this one Docker container. The read-only vs. read-write split is enforced by the mount itself (`:ro` vs. no suffix), at the kernel level, not by which `toolsets` a given delegation call was given. A subagent can't write to a `:ro`-mounted path no matter what it attempts.

- **Read-only document directories** (`/corpus/*`): mount each source directory separately with a `:ro` suffix. Don't try to unify scattered host directories with symlinks first — Docker bind-mounts don't follow a symlink to a target outside the mounted directory, so a symlink tree mounted alone will show up broken inside the container. Instead, mount each real source directory individually, choosing container-side paths that already look unified (`/corpus/collection-1`, `/corpus/collection-2`, etc.).
- **Deliverables directory** (`/workspace`): a plain read-write host bind mount. Persistence across sessions, projects, and chats is automatic and doesn't depend on `container_persistent` — that flag only affects in-container state (installed packages, etc.), not this bind-mounted data, which lives on the host disk regardless of container lifecycle.
- **`docker_run_as_host_user: true`**: without this, files written to `/workspace` come out root-owned on the host. Only works with the default `docker_image` (or plain Debian/Ubuntu-based images) — not with Hermes's own bundled image, which needs to start as root internally.
- **PDF text extraction**: the sandbox's base image has no PDF library preinstalled. Install once (persists for the container's lifetime, given `container_persistent: true`):
  ```bash
  docker ps -a | grep hermes-
  docker exec -u root <container_id> pip install pypdf pdfplumber
  ```
  A small reusable helper written to `/workspace/extract_pdf.py` (a plain host file via the bind mount) is a convenient way to give subagents a single consistent extraction command:
  ```bash
  docker exec <container_id> bash -c 'cat > /workspace/extract_pdf.py << "PYEOF"
  import sys, pdfplumber
  with pdfplumber.open(sys.argv[1]) as pdf:
      print("\n".join(p.extract_text() or "" for p in pdf.pages))
  PYEOF'
  ```
  Instruct subagents to use `python3 /workspace/extract_pdf.py <path>` rather than `read_file` on a PDF directly — `read_file` returns raw binary, which wastes time and tokens as the model works out on its own that it isn't usable text.

Leaf subagents (the kind `delegate_task` spawns by default) can't call Hermes's `memory` tool directly. The natural pattern for turning read documents into persistent knowledge: have the subagent read and summarize, return the summary to the primary, and let the primary decide what's worth writing to memory — this is also what keeps large raw documents out of the main context window in the first place.

## Persistent storage layout

| What | Where | Persists across updates? |
|---|---|---|
| Hermes config/env | `~/.hermes/config.yaml`, `~/.hermes/.env` | Yes |
| Cross-session memory | `~/.hermes/memories/` | Yes |
| Self-improving skills | `~/.hermes/skills/` | Yes |
| `SOUL.md` | `~/.hermes/SOUL.md` | Yes |
| Model weights | `~/.ollama/models` | Yes — independent of Hermes |
| Open WebUI chat history | Docker named volume `open-webui-data` | Yes — untouched by Hermes |
| Open Terminal sandbox state | Not persisted by design | Ephemeral, as intended |

For multi-agent instances to *share* memory/context, point Hermes at a pluggable memory provider backed by a local vector DB (Chroma/Qdrant) instead of the default SQLite store. Not necessary for a single-user setup.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Hermes refuses to initialize, context error | `model.context_length` should be auto-detected, not set | Leave `context_length` unset in `config.yaml`; only set it manually if auto-detection is wrong for this endpoint |
| Discord bot fails to connect: `discord.errors.PrivilegedIntentsRequired` in `gateway.error.log` | One or more of the three privileged intents (Presence, Server Members, Message Content) isn't enabled in the Developer Portal | Enable all three in the Bot tab, confirm it saved, then `hermes gateway restart` |
| Discord bot online and connected, but never replies to messages | `discord.require_mention: true` is the default — plain messages without an @mention are silently ignored, by design | @mention the bot explicitly, or set `discord.free_response_channels` for a channel where you don't want to |
| Ollama not responding, or one model evicting the other | `OLLAMA_MAX_LOADED_MODELS` not set or too low | Set to 2 in the brew-managed plist (§5), `brew services restart ollama` |
| Subagents run one after another instead of in parallel | `OLLAMA_NUM_PARALLEL` too low for `delegation.max_concurrent_children` | Raise `OLLAMA_NUM_PARALLEL` in the brew plist (§5) to match; verify with `ollama ps` under load |
| Gateway doesn't survive reboot | LaunchAgent not loaded, or Ollama not ready at login | Confirm `hermes gateway status` shows it supervised; confirm `brew services list` shows Ollama running |
| Subagents use the primary model instead of the fast one | `delegation:` isn't copied to `~/.hermes/config.yaml` | Confirm the block is present in `~/.hermes/config.yaml` |
| Subagent reports a source document path doesn't exist, but it's clearly on the host | Tried to unify scattered directories with symlinks before mounting — Docker bind-mounts don't follow a symlink to a target outside the mounted directory | Mount each real source directory individually in `terminal.docker_volumes` (§11), not a directory of symlinks pointing elsewhere |
| Sandboxed path request in Hermes Desktop reports "not found," but the same request via `hermes chat` or Discord works fine | Desktop doesn't reliably apply `terminal.backend: docker` — falls back to running on the host with no error shown (§10) | Use `hermes chat` or the Discord gateway for anything needing the Docker sandbox, until this is fixed upstream |
| Primary agent goes silent / no GPU activity right after announcing a tool call or delegation, or degenerates into repeated garbage tokens | A known class of inference-engine bugs affecting long, tool-call-shaped generations on this model — confirmed present on two different MLX-based servers (`mlx_lm.server`, `mlx_vlm.server`); not reproduced on Ollama under the same real workload | If this recurs on Ollama, capture the exact prompt/conditions and treat as a new report — the MLX-specific instances of this were resolved by moving to Ollama entirely, not by restarting the same backend |

## Security notes

- `.env` is gitignored (see below) — never commit it. `.env.example` in this repo has placeholders only.
- If you later expose the Discord bot beyond yourself (teammates/community), create a dedicated Hermes profile for it rather than sharing your personal CLI profile — isolates its skills, memory, and browser sessions.
- If you configure a cloud fallback via `config.yaml`'s `fallback_model:` block (commented out by default — see the bottom of `config/config.yaml`), traffic can leave the machine when local models fail repeatedly — set spend limits on that provider's API key if cost matters. Not currently enabled in this repo's `config.yaml`.

## `.gitignore` (add to repo root)

```
.env
*.log
```
