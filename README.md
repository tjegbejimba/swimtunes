# SwimTunes

SwimTunes is a desktop app for putting audio from public YouTube and SoundCloud links onto filesystem-mounted swim headphones. The current foundation is a shared Python package with an app-level workflow seam, replaceable external adapters, and a thin PySide6 GUI. The old `~/SwimTunes` PowerShell files are reference material, not the app.

## Locked v1 scope

- One shared Python desktop app, developed and tested on macOS, with a portable Windows 10/11 x64 build as the delivery target. No login, cookies, embedded credentials, browsing, or search. Search is a v2 feature.
- Paste one or more public YouTube video/playlist or SoundCloud track/playlist URLs. A video URL with `list=` still means one track; use an explicit playlist URL to request playlist expansion.
- Preview playlists with every item unchecked by default. Show errors per track, and process only selected items. There is no fixed playlist-size cap; check free space before work, warn when space is insufficient or uncertain, and let the user cancel.
- Bundle version-pinned yt-dlp and ffmpeg. Produce stereo 192 kbps constant-bitrate MP3 files. Extraction failures are errors, never successful transfers.
- On first run, ask for a headphones folder and remember it. Check the physical device identity before subsequent transfers; if the drive letter changes and identity cannot be confirmed, require explicit reselection. Do not write to a coincidentally matching path on a different device.
- Keep a separately selected local library optional and off by default. Offline downloads are available only when keep-local is enabled. Keep a completed MP3 available for retry after a failed transfer even when keep-local is off; that retry copy is not an offline library.
- Skip duplicates using the source identity, handle different sources with the same title without overwriting, and recover from partial copies. Cancel stops remaining work but retains completed tracks. A download counts as transferred only after a complete copy reaches the selected headphones folder.

## Release gates

SwimTunes source stays in this **private** repository. Publish signed portable Windows binaries and signed update metadata in the separate **public** `tjegbejimba/swimtunes-releases` repository, along with any required third-party notices, build details, and corresponding source. Never publish SwimTunes app source there or embed a private-repo token in the app.

Before any friend-facing release, choose an FFmpeg build with suitable license terms and prepare the matching license notices, build details, and corresponding FFmpeg source as required by that build and its dependencies. Check the [FFmpeg distribution guidance](https://www.ffmpeg.org/legal.html). Build the Windows target, pin and bundle extraction tools, sign the release with the designated publisher, and have the startup updater verify the trusted signature and publisher of both the update information and the new build before accepting it. If fetching or verification fails, warn and keep running the current signed version; do not treat a failed update as success.

macOS tests do not establish Windows playback or headphones compatibility. No actual Windows/headphones test is available yet, so the first release must say **beta** and must not claim verified playback. YouTube extraction is unofficial and YouTube's terms restrict third-party downloading; a public URL is not permission to bypass those terms. See [ADR 0003](docs/adr/0003-unofficial-extraction-boundary.md).

## Development

Use `uv` from the repository root:

```bash
uv run pytest
uv run swimtunes
```

## Portable Windows development artifact

GitHub Actions runs `uv run pytest -q` on macOS and Windows. Pushes also build an internal Windows x64 portable layout with a stable root launcher (`SwimTunes.exe`) and the current PyInstaller onedir app under `versions/<version>/SwimTunes`. Pinned yt-dlp and FFmpeg binaries are downloaded in CI from exact URLs and verified by SHA256. The bundle includes `tools/TOOL_MANIFEST.json` and `THIRD_PARTY_NOTICES.md`, and CI smoke-runs the root launcher with `--self-check` to verify the frozen app can resolve its bundled tools without a system Python install. This artifact is internal only and is not uploaded to any public release repository.

Tests must not hit live YouTube or SoundCloud. Workflow tests inject deterministic adapters at `swimtunes.app.Workflow`; real media-shape coverage uses locally generated `ffmpeg` fixtures and `ffprobe`.
