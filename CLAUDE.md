# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Jerry is a single-file POSIX shell script (`jerry.sh`, ~2150 lines) that lets users watch anime and read manga synced with their AniList account. It tracks progress down to the second (like YouTube/Netflix), supports multiple streaming providers, and can use fzf, rofi, or a native GTK GUI (`jerry_gui.py`) for selection menus.

## Running and testing

There is no build system, test suite, or linter. To test changes:

```sh
bash jerry.sh [options] [query]   # run directly without installing
bash -n jerry.sh                  # syntax check only
shellcheck jerry.sh               # static analysis (if shellcheck is installed)
bash jerry.sh --trace ...         # verbose [TRACE] logging to stderr for every scraping step
```

Because fzf/rofi require an interactive TTY, testing a specific provider function non-interactively is easier by extracting it with `awk`/`sed` into a scratch file and sourcing it with stub `curl`/`launcher` implementations, rather than driving the full CLI. `--trace` output is the fastest way to see where in the search → episode-fetch → decrypt chain something broke.

To install locally for manual testing:
```sh
sudo cp jerry.sh /usr/local/bin/jerry && sudo chmod +x /usr/local/bin/jerry
```

To generate/edit the config:
```sh
jerry -e
```

Config file lives at `~/.config/jerry/jerry.conf` (or `$XDG_CONFIG_HOME/jerry/jerry.conf`). See `examples/jerry.conf` for all defaults.

## Architecture

Everything lives in `jerry.sh`. Key sections and their flow:

**Startup / configuration** (top of file)
- Platform detection: macOS uses `gsed`/`iina`; Windows (MINGW) uses `mpv.exe` with `;` as PATH separator; Android is detected via `uname -o`
- `configuration()` sources the config file and sets defaults for all variables
- `check_credentials()` handles AniList OAuth token storage in `~/.local/share/jerry/`
- Dependency checks (`dep_ch`) run at load time, including detecting whether `botan` or `botan3` is installed and which subcommand syntax it exposes (`cipher` on botan3, `encryption` on botan2) — this drives `get_aa_req()`

**Provider scraping** (per-provider functions)
- `allanime` (default): `get_episode_info()` searches allanime.day, `get_json()` fetches the episode's encrypted payload, `decode_tobeparsed()` (AES-256-CTR via openssl) decrypts the response, `provider_init()` runs an obfuscated hex-substitution decode on the resulting source IDs, then `generate_links()` dispatches to per-streamer link extraction
- AllAnime's API requires an **encrypted request** on top of the encrypted response: `get_aa_req()` builds an AES-256-GCM-encrypted auth blob (via `botan`) from `allanime_key`/`allanime_epoch`/`allanime_build_id`/`allanime_query_hash`, sent as `aaReq` in the GraphQL `extensions` field with an `x-build-id` header. These constants come from AllAnime's server and drift over time (breaking with errors like `AA_CRYPTO_MISSING`/`AA_CRYPTO_STALE`) — when the provider stops returning sources, check upstream `pystardust/ani-cli` for updated values before assuming the local decode logic is wrong
- AllAnime's search matches on exact whole words (not fuzzy) — a title from AniList that differs even slightly (hyphenation, spacing, season numbering, e.g. "2nd Season" vs "Season 2") returns zero results. `get_episode_info()`'s allanime branch retries with a fallback query (text before the first colon, or first 3 words) before giving up
- `aniwatch`, `yugen`, `hdrezka`, `crunchyroll`, `aniworld`: each has its own fetch/parse chain, several looking up cross-site IDs via `get_mal_backup()` (fetches `bal-mackup/mal-backup` JSON keyed by AniList ID)
- All providers ultimately produce a link fed to `select_quality()` → mpv/player

**AniList API** (GraphQL via curl)
- `get_anime_from_list()` / `get_manga_from_list()`: fetch the user's watching/reading list
- `get_airing_today()`: query AniList airing schedule for today with day-navigation pagination — feeds the "Airing Today (Anime)" mode, which auto-clamps to the latest episode actually available on the provider (not necessarily what AniList's episode count implies)
- `get_recently_updated_manga()`: paginated recently updated manga from AniList
- `search_anime_anilist()` / `search_manga_anilist()`: title search
- `update_anime_list()` / `update_manga_list()`: POST progress/score updates after episode/chapter completion

**UI abstraction**
- `launcher()`: wraps fzf (terminal), rofi (external menu), or `jerry_gui.py` (`--gui`, native GTK3 picker) — all user selections go through this
- `jerry_gui.py`: standalone GTK3 script invoked via `python3 "$gui_script"`; IMAGE mode shows a cover-art grid from tab-delimited `cover_url\tmedia_id\ttitle` stdin, LIST mode is a plain searchable list — both print the selected line to stdout
- `image_preview_fzf()`: fzf with ueberzugpp or chafa for cover art previews
- `select_desktop_entry()`: rofi with `.desktop` files for image preview in external menu mode
- `download_thumbnails()`: parallel curl downloads of cover images to `/tmp/jerry/jerry-images/`

**Playback loop**
- `binge()`: the main loop — increments episode/chapter, launches the player, waits for exit, updates AniList progress, loops. Also detects when the "Airing Today" episode clamp pulled `progress` *backward* (meaning no new episode is out yet) and breaks instead of silently replaying the last-watched episode
- Player position is tracked via `/tmp/jerry_position` (written by mpv's `--term-status-msg`)
- `jerrydiscordpresence.py`: optional Python helper that wraps mpv and polls `/tmp/jerry_position` to update Discord RPC

**State variables** (set by selection functions, consumed by `binge`)
- `media_id`, `title`, `progress`, `episodes_total`, `score`, `start_year`
- `mode_choice` drives the `case` in `main()` to pick which selection function runs — note it stays set to whatever the user picked (e.g. `"Airing Today (Anime)"`) for the entire binge session, not just the first episode

**`main()` flow**
1. Parse args → set `mode_choice`
2. If anilist enabled: `check_credentials()` then show menu (Watch Anime / Read Manga / Airing Today / etc.)
3. Mode-specific function sets `media_id`, `title`, `progress`
4. `binge "ANIME"` or `binge "MANGA"` runs the playback loop

## Key conventions

- `$sed` variable (not `sed` directly) — ensures `gsed` is used on macOS
- Tab-delimited internal data format: `cover_url\tmedia_id\ttitle` for list items passed to `launcher` and thumbnail functions
- `send_notification()` prints to stdout in terminal mode, uses `notify-send` in external menu mode, and is suppressed entirely in `--json` mode
- `trace_log()` prints `[TRACE] ...` to stderr only when `--trace`/`trace_enabled=true`; used heavily in the allanime provider chain since failures there are otherwise silent
- Temp files go in `/tmp/jerry/`; cleaned up on EXIT/INT/TERM via `trap cleanup`
- AniList client ID is hardcoded as `9857` (OAuth implicit flow, token stored locally)
