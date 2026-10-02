# Startup update eligibility through signed public releases

At startup, SwimTunes checks the public GitHub Releases feed in `tjegbejimba/swimtunes-releases` anonymously over HTTPS. The feed adapter fetches a signed canonical JSON manifest and the named Windows artifact without using a token or executing downloaded content.

The app decides only whether a candidate is eligible; installation is a later installer concern. A candidate must be a proper semantic-versioned newer build, have Ed25519-signed metadata verified by a public key configured at build time, match the signed artifact name, size, SHA256, and publisher fields, and pass a platform Authenticode publisher check against the configured designated publisher. The Windows publisher check lives behind an adapter so tests can use fakes and macOS tests never require a real signed executable.

Offline feeds, malformed or unsigned metadata, bad signatures, hash or size mismatches, wrong publisher signatures, and same-or-older versions keep the current app running. Trust failures produce visible warnings; offline fetch failures are warnings rather than startup blockers. Real signing keys and the designated publisher certificate are release inputs for the signing/release ticket; private keys are never embedded.
