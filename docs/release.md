# SwimTunes signed beta release runbook

This runbook is for the first public beta in `tjegbejimba/swimtunes-releases`. Do not publish unsigned artifacts, app source, private keys, or broad-scope tokens.

## One-time setup

1. Provision a designated Authenticode publisher identity.
   - Preferred: Azure Artifact Signing / Trusted Signing.
     - Repository secrets: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`.
     - Repository variables: `AZURE_ARTIFACT_SIGNING_ENDPOINT`, `AZURE_SIGNING_ACCOUNT_NAME`, `AZURE_CERTIFICATE_PROFILE_NAME`.
   - Alternative OV/PFX mode:
     - Repository secrets: `WINDOWS_SIGNING_PFX_BASE64`, `WINDOWS_SIGNING_PFX_PASSWORD`.
     - Dispatch the workflow with `signing_mode=pfx`.
2. Generate the Ed25519 update-manifest keypair locally, outside the repository, and keep the private key secret:

   ```bash
   mkdir -p release-keys
   uv run python - <<'PY'
   import base64
   from pathlib import Path
   from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
   from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

   out = Path("release-keys")
   private_key = Ed25519PrivateKey.generate()
   out.joinpath("swimtunes_update_private_key.base64").write_text(
       base64.b64encode(private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode("ascii") + "\n",
       encoding="utf-8",
   )
   out.joinpath("swimtunes_update_public_key.base64").write_text(
       base64.b64encode(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii") + "\n",
       encoding="utf-8",
   )
   PY
   ```

   Store the private value as secret `SWIMTUNES_UPDATE_PRIVATE_KEY` and the public value as variable `SWIMTUNES_UPDATE_PUBLIC_KEY`.
3. Store the exact expected certificate subject fragment as variable `SWIMTUNES_UPDATE_PUBLISHER`. It must match the Authenticode signer subject verified by the workflow and by the app updater.
4. Create a protected GitHub Actions environment named `release`. Require a human approval before the `publish` job can run.
5. Create a narrowly scoped token for the public `tjegbejimba/swimtunes-releases` repository, with only the release/content permissions needed to create a release and upload assets. Store it as secret `SWIMTUNES_RELEASES_PUBLISH_TOKEN`.
6. Recheck the exact FFmpeg build in `docs/third-party.md` against FFmpeg/Gyan.dev obligations. Prepare or link the matching notices, build details, and corresponding source/written-offer material required for the shipped build.

## Dry run

1. Dispatch **Signed beta release** with:
   - `version`: the planned prerelease version, e.g. `0.2.0-beta.1`
   - `signing_mode`: `azure` unless using the documented PFX fallback
   - `dry_run`: `true`
2. Confirm the workflow:
   - refuses missing signing/update secrets instead of falling back to unsigned output,
   - signs every `.exe` and `.dll`,
   - verifies signatures and the configured publisher,
   - signs `update-manifest.json` and verifies it with the embedded public key,
   - audits the payload for private app source (`.py`, `src/`, `tests/`),
   - uploads the `signed-public-release-payload` artifact only.

## Live beta release

1. Dispatch the workflow with the same version and `dry_run=false`.
2. Approve the protected `release` environment only after reviewing the dry-run artifact contents. The public release must contain only:
   - `SwimTunes-<version>-windows-x64.zip`,
   - `update-manifest.json`,
   - `update-manifest.json.sig`,
   - `THIRD_PARTY_NOTICES.md`,
   - `TOOL_MANIFEST.json`,
   - `FFMPEG_SOURCE_DETAILS.md`.
3. Confirm the public GitHub release is marked pre-release/beta and the notes explicitly say playback on actual swim headphones is unverified. Do not claim physical-headphones compatibility.
4. Exercise the live feed before announcing:
   - install the previous signed beta,
   - publish the new signed beta,
   - start the previous beta and confirm it sees and installs the newer signed update,
   - simulate a failed update and confirm startup falls back to the existing runnable version with a warning.

If signing enrollment, scoped token setup, FFmpeg redistribution materials, or live updater fallback checks are incomplete, do not announce the release.
