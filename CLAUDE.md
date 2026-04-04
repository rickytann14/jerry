# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Jerry is a single-file POSIX shell script (`jerry.sh`) that lets users watch anime and read manga synced with their AniList account. It tracks progress down to the second (like YouTube/Netflix), supports multiple streaming providers, and can use fzf or rofi for selection menus.

## Running and testing

There is no build system, test suite, or linter. To test changes:

```sh
bash jerry.sh [options] [query]   # run directly without installing
bash -n jerry.sh                  # syntax check only
shellcheck jerry.sh               # static analysis (if shellcheck is installed)
```

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

Everything lives in `jerry.sh` (~1600 lines). Key sections and their flow:

**Startup / configuration** (top of file)
- Platform detection: macOS uses `gsed`/`iina`; Windows (MINGW) uses `mpv.exe` with `;` as PATH separator
- `configuration()` sources the config file and sets defaults for all variables
- `check_credentials()` handles AniList OAuth token storage in `~/.local/share/jerry/`

**Provider scraping** (per-provider functions)
- `allanime` (default): uses `get_links()` → `generate_links()` → `provider_init()` with an obfuscated decode step
- `aniwatch`, `yugen`, `hdrezka`, `crunchyroll`: each has its own fetch/parse chain
- All providers ultimately produce a link fed to `select_quality()` → mpv/player

**AniList API** (GraphQL via curl)
- `get_anime_from_list()` / `get_manga_from_list()`: fetch the user's watching/reading list
- `get_airing_today()`: query AniList airing schedule for today with day-navigation pagination
- `get_recently_updated_manga()`: paginated recently updated manga from AniList
- `search_anime_anilist()` / `search_manga_anilist()`: title search
- `update_anime_list()` / `update_manga_list()`: POST progress/score updates after episode/chapter completion

**UI abstraction**
- `launcher()`: wraps fzf (terminal) or rofi (external menu) — all user selections go through this
- `image_preview_fzf()`: fzf with ueberzugpp or chafa for cover art previews
- `select_desktop_entry()`: rofi with `.desktop` files for image preview in external menu mode
- `download_thumbnails()`: parallel curl downloads of cover images to `/tmp/jerry/jerry-images/`

**Playback loop**
- `binge()`: the main loop — increments episode/chapter, launches the player, waits for exit, updates AniList progress, loops
- Player position is tracked via `/tmp/jerry_position` (written by mpv's `--term-status-msg`)
- `jerrydiscordpresence.py`: optional Python helper that wraps mpv and polls `/tmp/jerry_position` to update Discord RPC

**State variables** (set by selection functions, consumed by `binge`)
- `media_id`, `title`, `progress`, `episodes_total`, `score`, `start_year`
- `mode_choice` drives the `case` in `main()` to pick which selection function runs

**`main()` flow**
1. Parse args → set `mode_choice`
2. If anilist enabled: `check_credentials()` then show menu (Watch Anime / Read Manga / Airing Today / etc.)
3. Mode-specific function sets `media_id`, `title`, `progress`
4. `binge "ANIME"` or `binge "MANGA"` runs the playback loop

## Key conventions

- `$sed` variable (not `sed` directly) — ensures `gsed` is used on macOS
- Tab-delimited internal data format: `cover_url\tmedia_id\ttitle` for list items passed to `launcher` and thumbnail functions
- `send_notification()` prints to stdout in terminal mode, uses `notify-send` in external menu mode, and is suppressed entirely in `--json` mode
- Temp files go in `/tmp/jerry/`; cleaned up on EXIT/INT/TERM via `trap cleanup`
- AniList client ID is hardcoded as `9857` (OAuth implicit flow, token stored locally)
