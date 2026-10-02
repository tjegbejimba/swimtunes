# Third-party inventory for internal Windows portable builds

This inventory covers the portable Windows app folder produced by CI. Unsigned CI artifacts are internal development outputs only. The signed beta release workflow copies this inventory into the public payload only after Authenticode signing, manifest signing, publisher verification, payload audit, and protected release approval.

## Bundled command-line tools

| Component | Pinned version | CI source | SHA256 | License / obligations |
| --- | --- | --- | --- | --- |
| yt-dlp executable | 2026.08.19 | <https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp.exe> | `66674953fe251b89f4d08c5f0e35e0728679bd67ab3d7d05c0562af101dd3e7a` | The yt-dlp project is dedicated to the public domain under the Unlicense. Preserve upstream notices and the exact source URL/version in the bundle manifest. |
| FFmpeg Windows build | 9.0.2 Gyan.dev essentials build | <https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip> | `60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba` | This is a clearly documented GPL FFmpeg distribution choice for internal builds. Before public distribution, publish required license notices, build details, and corresponding source / written offer material for FFmpeg and enabled libraries as required by the build. Review FFmpeg legal guidance and Gyan.dev build metadata for the exact release. |

The CI bundle includes `tools/TOOL_MANIFEST.json`, generated from the pinned URLs and hashes above, and a copy of this file as `THIRD_PARTY_NOTICES.md`. The public beta payload also includes `FFMPEG_SOURCE_DETAILS.md` with the exact bundled FFmpeg binary URL/hash and source/build references for release review.

## Python runtime dependencies included by PyInstaller

The portable folder also contains the Python interpreter and Python packages resolved by `uv.lock`, including PySide6, shiboken6, pytest-only development tools excluded from normal runtime imports, and the pinned `yt-dlp==2026.8.19` Python package. Treat `uv.lock` as the version inventory for Python wheels. Before a public release, generate a complete wheel license report from the locked environment and add it to the release notices.

## Distribution constraints

- The artifact is for private/internal development validation only.
- Do not upload unsigned artifacts, private application source, or any private-repository credential to the public releases repository.
- Future installer/updater tickets must keep tool downloads in CI/release production only; the app must not download or self-update yt-dlp or FFmpeg at runtime.
