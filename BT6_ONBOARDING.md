# BT6 ONBOARDING

> *You're one of ~10 trusted operators looking at Pliny Command before it
> goes wider. This file gets you from a fresh clone to your first gauntlet
> trophy in about 10 minutes.*

---

## What this is

Pliny Command is a single-binary research surface — one Python file serves
three dashboards on `localhost:8888` and orchestrates Claude agents, the
gauntlet jailbreak speedrun, ouroboros loops, multi-agent "lairs", a village
of persistent dragon agents, and a watchdog/fixer that keeps the lights on.

Read [`README.md`](README.md) for the long form. This file is the **path
through it** the first time.

---

## 10-minute setup

You'll need: Python ≥3.9, ~500 MB of free space, an OpenRouter key, and
optionally Claude Code installed.

### 1. Clone + install

```bash
git clone https://github.com/dragons-blood/libertarium-dev.git pliny-command
cd pliny-command
./quickstart.sh
```

`quickstart.sh` is idempotent. It installs `pyyaml` (the only hard dep),
creates `state/ sessions/ logs/ rt_library/`, seeds the gauntlet starter
configs, and writes `.env` from the template if one doesn't exist.

### 2. Set the one required key

Open `.env` and set:

```
OPENROUTER_API_KEY=sk-or-v1-...
```

That's it for minimum-viable launch. Get a key at
<https://openrouter.ai/keys>. Without it the gauntlet has no judge and
no Hermes fallback attacker.

### 3. (Strongly recommended) install Claude Code

```bash
# follow https://docs.claude.com/en/docs/claude-code
claude --version  # should print a version
```

Pliny Command was built on **Claude Code + Opus 4.6**. Sessions, ouroboros,
the lair, the village, and the watchdog fix-agent all spawn `claude` as
a subprocess. Without it those features won't do much.

### 4. (Optional) provider keys

Add any of these to `.env` to unlock more gauntlet targets:

```
OPENAI_API_KEY=...        # gpt-5.4 native
ANTHROPIC_API_KEY=...     # claude-opus-4-6 via API instead of CLI
GOOGLE_API_KEY=...        # gemini-3.1-pro-preview
XAI_API_KEY=...           # grok-4.20
MISTRAL_API_KEY=...       # mistral-large
DASHSCOPE_API_KEY=...     # qwen
```

Missing keys are fine — the gauntlet silently skips those targets.

### 5. Launch

```bash
make run
# or
./quickstart.sh           # also opens your browser
```

You should see a banner like:

```
╔══════════════════════════════════════════╗
║     PLINY COMMAND — Mission Control      ║
║   http://localhost:8888                  ║
║   Version: 0.1.0-beta.1                  ║
║   Workshop: /home/you/pliny-workshop     ║
╚══════════════════════════════════════════╝
```

Open <http://localhost:8888>. You should see Mission Control with a
version badge in the topbar.

---

## First trophy — 5-minute path

1. Click **BLOOD AGENT** in the top nav (`/redteam`).
2. The setup overlay should be gone now. You'll see the gauntlet launcher.
3. **Pick a preset**: `l1b3rt4s` is the default and the most-tested one.
4. **Pick a mode**: 🎯 **SINGLE-MODEL** is the simplest first run — one
   target, all 16 harm behaviors.
5. **Pick a target**: pick something you have a key for, or any model
   routed through OpenRouter (`glm-5-1`, `qwen-3-6-plus`, `llama-4-maverick`).
6. **Parallelism**: start at `2`. You can go to 16 later.
7. **LAUNCH**. The scoreboard should start populating within ~30 seconds.
8. When a target cracks, click its row — the trophy modal shows the
   verbatim winning prompt and response.

If it sits at 0/16 for more than 5 minutes, something's wrong. Check
`logs/server.log` and file an issue (see below).

---

## The three dashboards

| URL | What |
|---|---|
| `/` | **Mission Control** — sessions, comms, missions, schedules, ouroboros, computer use, watchdog, village board |
| `/redteam` | **THE GAUNTLET** — preset launcher, live scoreboard, trophies |
| `/arcade` | **The Arcade** — replays, lair (multi-agent collab), village pixel-map, leaderboards |

---

## What's known to be rough

These are documented gaps, not bugs to report:

- **Mac-first**: Pliny runs on macOS. Linux works for the gauntlet, sessions,
  ouroboros. Computer-use (`computer_use.py`) and the agent-browser
  (`agent_browser.py`) assume macOS paths and may not work on Linux.
- **The lore is dense.** Dragons, departments, the village, the phylactery,
  the watchtower — all of this is built around Pliny's research workflow.
  You don't need to use any of it to run the gauntlet. Treat it as a tour.
- **No first-run wizard yet.** You're editing `.env` by hand. This is on
  the BT6 punchlist.
- **No multi-tenancy.** Designed for one operator per instance. Binds to
  `127.0.0.1`. Don't expose to a public interface.
- **State recovery is blunt.** `make clean` wipes everything except
  `rt_library/`. `make nuke` wipes everything. Per-subsystem resets are
  on the roadmap.
- **The HTML files are duplicated.** Shared CSS lives in three places.
  Cosmetic, not blocking.

---

## How to report findings

**Open a GitHub Issue with the `bt6` label.** Template:

- **What you did**: clone → ran `make` → clicked X → expected Y → got Z
- **Version**: copy from `VERSION` (also visible in dashboard topbar)
- **Logs**: attach `logs/server.log` (last 200 lines is usually enough)
- **`.env` keys set**: list the keys you've set (do NOT paste values)

Severity hints:

- `bt6/p0` — crashes, data loss, can't launch at all
- `bt6/p1` — feature doesn't work as documented
- `bt6/p2` — friction, confusion, "I expected X to happen here"
- `bt6/p3` — nits, cosmetics, doc fixes

If you find a real security issue (auth bypass, RCE, secrets exposure),
**don't** open a public issue — DM Pliny directly.

---

## Useful Make targets

```bash
make              # install + run
make smoke-test   # quick check: deps + bootability without launching
make run          # launch with .env loaded
make stop         # kill server on :8888
make status       # show pid + url
make clean        # wipe sessions/, logs/, state/ (keeps rt_library)
make nuke         # wipe EVERYTHING (asks YES first)
```

---

## You're done. What now?

- Run the gauntlet against one target you care about. Use the verbatim
  trophy as your repro.
- Open one PR-sized issue: a confusing thing you hit, a doc gap, a feature
  you wish existed.
- Don't try to use every subsystem on day 1. Sessions, the gauntlet, and
  Mission Control are the load-bearing parts. Everything else is optional.

🐉
