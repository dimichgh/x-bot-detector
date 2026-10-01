# x-bot-detector

Find **coordinated follow-farm / bot clusters on X (Twitter)**. Give it one handle or a group of
handles. It looks for the pattern of accounts created in late 2024–2026 that already have large
follower counts and sit inside dense mutual-follow clusters of similarly-aged accounts.

No single signal proves automation, so the tool stacks many weak signals and puts the most
weight on **network structure**: who follows whom, and when those accounts were created.
That structure is much harder to fake than profile text.

## Quick start

No cloning needed. With [uv](https://docs.astral.sh/uv/) installed, this one line downloads the
tool and runs it:

```bash
uvx --from git+https://github.com/dimichgh/x-bot-detector xbot analyze @handle1 @handle2
```

It prints a summary in the terminal and writes an HTML report to `./xbot-reports/`.
To keep the `xbot` command around, see [Install](#install).

By default this needs **no X login and no API key**. It uses the free
[FxTwitter API](https://github.com/FxEmbed/FxEmbed). Optionally it can use **your own logged-in X
browser session** (via [twscrape](https://github.com/vladkens/twscrape)) or the
[`bird`](https://www.npmjs.com/package/@steipete/bird) CLI that OpenClaw uses.

## What it checks

| Layer | Signals |
|---|---|
| **Account metadata** | account age vs. follower growth (followers/day on young accounts) · following ≫ followers, accounts pinned at the 5,000-follow cap, 1:1 follow-back shape · inhuman posting or like volume · large audience with almost no posts · default avatar / empty bio / no banner · auto-generated `name12345678` handles · paid check on a brand-new account |
| **Origin** (X's *About this account*) | country X says the account is based in vs. profile location vs. signup app-store region · VPN/proxy flag · handle renames (a rename right after sign-up is ignored) |
| **Behaviour** (recent timeline) | share of reposts (pure amplifiers) · no sleep gap across UTC hours · clockwork posting intervals · templated / duplicate posts · automation clients · who it amplifies |
| **Follower neighbourhood** | share of followers created in the new wave · densest 30-day creation window among followers (very fresh sign-ups are discounted because X onboarding pushes them to follow accounts) · **follower-map bands**: long runs of consecutive followers all created within the same few weeks, the classic signature of purchased or farmed followers · share of bot-like followers · follow-back rate |
| **Follow graph clusters** | expands into the most suspicious neighbours (new, sizeable, mutual with a seed, shared between seeds), fetches *their* follow lists, then runs Louvain community detection. Clusters are scored on density, mutual follows, shared follow targets (Jaccard), creation-date cohort and member bot-likeness |
| **Seed group** | when you pass several handles: pairwise follower/following overlap, whether they follow each other, how close their creation dates are, shared amplification, and a cluster score for the group itself |
| **Engagement sets** | for a post: reposters and repliers, their creation-date concentration (>~40% in one window is highly anomalous), share of new or bot-like engagers, reply velocity, copy-paste replies |

Signals combine as a noisy-OR, `1 − Π(1 − strength × weight)`, so several weak signals stack
into a strong one while no single weak signal dominates. Levels: low < 30 ≤ moderate < 50 ≤
high < 70 ≤ very high.

**Selection-bias corrections.** Expanded neighbours are chosen *because* they are connected to a
seed and look new and bot-like. Cluster metrics therefore:

- exclude the seed's own edges;
- compare a cluster's creation cohort and new-wave share against the whole expanded pool;
- scale the composition signals (created together, new, bot-like) by how much real follow
  structure the cluster has.

A seed is then scored on how tightly it is tied into each cluster.

## Install

Requires Python ≥ 3.10. Install the `xbot` command straight from GitHub with
[pipx](https://pipx.pypa.io) or [uv](https://docs.astral.sh/uv/). Both put it in its own isolated
environment, so nothing clashes with your other Python packages:

```bash
pipx install git+https://github.com/dimichgh/x-bot-detector
# or
uv tool install git+https://github.com/dimichgh/x-bot-detector

xbot analyze @handle1 @handle2
```

If your shell says `xbot: command not found`, run `pipx ensurepath` (or `uv tool update-shell`)
and open a new terminal.

To also use your logged-in X session (see [Using your browser session](#using-your-browser-session)),
install the optional extras:

```bash
pipx install "x-bot-detector[twscrape,browser] @ git+https://github.com/dimichgh/x-bot-detector"
# or
uv tool install "x-bot-detector[twscrape,browser] @ git+https://github.com/dimichgh/x-bot-detector"
```

- `twscrape`: fetch data through your X session cookies.
- `browser`: read those cookies straight from Chrome, Firefox or Safari.

| | pipx | uv |
|---|---|---|
| Update to the latest version | `pipx upgrade x-bot-detector` | `uv tool upgrade x-bot-detector` |
| Uninstall | `pipx uninstall x-bot-detector` | `uv tool uninstall x-bot-detector` |

<details>
<summary>Plain pip, or from a local checkout</summary>

```bash
python -m venv .venv && . .venv/bin/activate
pip install "x-bot-detector[twscrape,browser] @ git+https://github.com/dimichgh/x-bot-detector"

# or, to change the code:
git clone https://github.com/dimichgh/x-bot-detector && cd x-bot-detector
pip install -e '.[dev,twscrape,browser]'
```

</details>

## Usage

```bash
# Full analysis: metadata + followers/following + timeline + 2nd-degree cluster expansion
xbot analyze @MartinKuban @OxaraElena

# A list of handles (one per line, # comments allowed), bigger samples
xbot analyze -f handles.txt --followers 2000 --following 2000 --expand 60

# Quick metadata/timeline scoring for many accounts (no follower graphs)
xbot profile @a @b @c

# Who amplified a post?
xbot engagement https://x.com/someone/status/1234567890 --reposts 500 --replies 300

# Save raw data, then re-analyse offline with different parameters
xbot analyze @someone --save-dataset
xbot report xbot-reports/<run>/dataset.json.gz --recent-since 2025-01-01 --window-days 14
```

Every run prints a terminal summary and writes `report.html` (self-contained, with follower
maps, creation histograms and the follow network, in light and dark mode), `report.md` and
`report.json` to `./xbot-reports/<date>-<handles>/`. Change this with `--out` and
`--format html,md,json,csv,gexf,graphml`. The `gexf`/`graphml` exports open in
[Gephi](https://gephi.org/) with score, cluster, creation date and roles as node attributes.

Main knobs (`xbot analyze -h` for all):

| Option | Default | Meaning |
|---|---|---|
| `--followers N` / `--following N` | 1000 | how much of each seed's lists to sample (newest first) |
| `--timeline N` | 100 | recent timeline items per seed for behaviour analysis |
| `--expand N` | 30 | neighbours whose own following lists are fetched to measure cluster density |
| `--expand-following N` | 400 | following entries per expanded neighbour |
| `--expand-scope recent\|all` | recent | expand only new-wave neighbours, or any |
| `--recent-since DATE` | 2024-10-01 | start of the "new wave" |
| `--window-days N` | 30 | creation-date clustering window |

A default two-handle run makes about 400 requests and takes 1–2 minutes. Responses are cached
in `~/.xbotdetect/cache.sqlite3` for 24h (`--cache-ttl`, `--no-cache`).

## Data sources

Choose with `--source` (a comma-separated list is tried in order, falling back on errors):

| Source | Login | Provides |
|---|---|---|
| `fxtwitter` *(default)* | none | profiles, About-this-account, followers, following, timelines, reposters, replies |
| `twscrape` | your X session cookies | everything above, plus repost timestamps and posting-client labels |
| `bird` | your browser's X session (automatic) | followers, following, About, timelines (profiles come from FxTwitter) |
| `offline` | none | replays a `--save-dataset` file |

### Using your browser session

Pick one:

1. **Read cookies from your browser** (needs the `[browser]` extra; works on the machine where
   you are logged in to x.com):
   ```bash
   xbot analyze @someone --source twscrape --cookies-from-browser chrome
   ```
2. **Paste the two cookies.** Open x.com, then DevTools → Application → Cookies → `https://x.com`,
   and copy `auth_token` and `ct0`:
   ```bash
   export X_AUTH_TOKEN=...  X_CT0=...
   xbot analyze @someone --source twscrape,fxtwitter
   ```
   `--cookies "auth_token=...; ct0=..."` and `--cookies-file` (a Cookie header, a JSON export from a
   cookie extension, or a Netscape `cookies.txt`) also work. `bird`'s `AUTH_TOKEN`/`CT0` variables
   are accepted too.
3. **Use `bird`** (`npm i -g @steipete/bird`). It finds your Safari/Chrome/Firefox X session itself:
   ```bash
   xbot analyze @someone --source bird            # add --cookies-from-browser firefox to pick a browser
   ```

Cookies are passed to the backends via environment variables or twscrape's local accounts
database (`~/.xbotdetect/twscrape-accounts.db`), never on a command line. twscrape's anonymous
telemetry is switched off unless you set `TWS_TELEMETRY` yourself. A session cookie gives full
access to your X account, so keep it private, and consider a secondary account for heavy crawling.

## Reading the results

- **Seed score.** The account's own signals plus its neighbourhood and cluster ties. The HTML
  card shows every signal that fired, with the numbers behind it.
- **Follower map.** Each dot is a sampled follower: x = follow order (oldest left), y = when that
  account was created. Organic audiences scatter. Shaded boxes mark *bands* of consecutive
  followers created in the same window. Boxes outlined in grey are fresh sign-ups and may just be
  X onboarding.
- **Clusters.** Density, mutual follows and shared follow targets are measured among the
  neighbours, without the seed's edges. *Seed ties* shows how many members each seed has mutual,
  outgoing and incoming follows with. *Jointly followed* lists the accounts the cluster boosts
  together.
- **Most suspicious neighbours.** Ranked accounts around the seeds, with the role in which each
  was seen.

### Caveats

- These are suspicion indicators, not proof. Political and news accounts can grow fast through
  controversy or coordinated *human* amplification. Follow-back communities and genuine new users
  also trip individual signals. Check context before drawing conclusions about any person.
- Lists are samples, so densities and overlaps are lower bounds. Raise `--followers`,
  `--expand` and `--expand-following` for more confidence.
- FxTwitter is a free community service, so be gentle with it. The tool rate-limits itself,
  caches responses and backs off on errors. Automated access to X may conflict with X's terms;
  you are responsible for how you use it.

## Development

```bash
git clone https://github.com/dimichgh/x-bot-detector && cd x-bot-detector
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest            # synthetic follow-farm fixtures, mocked HTTP, CLI round-trips
ruff check src tests
```

Layout: `sources/` (backends + cache), `collect.py` (crawling and candidate expansion),
`analysis/` (account, behaviour, temporal, network, engagement scoring), `report/` (text,
Markdown, HTML/SVG, exports), `cli.py`.
