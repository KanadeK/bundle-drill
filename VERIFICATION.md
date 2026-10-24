# Verification

Release gates run on Windows with Python 3.13 and Git 2.56.0:

- 28 unit/integration/CLI tests, using real full and incremental Git bundles.
- SHA-1 and SHA-256 restore chains; annotated tag object identity; expected-ref
  mismatch; exact/nonexact contracts; missing/out-of-order/truncated bundles.
- Input preservation and scratch cleanup on success, restore failure and limits.
- Injected Git repository/config/alternate environment variables do not affect drills.
- Malformed/duplicate/ref-injection headers, filtered bundles, HEAD-only bundles,
  missing Git, duplicate contract keys and nonfinite timeout values are rejected.

Review explicitly scoped ref deletion, LFS and decompressed disk limits in README.
The CLI never prints raw Git diagnostics. Git operations are restricted to scratch
repositories. No third-party repository histories or code fixtures are shipped.

See GitHub Actions for Ubuntu/Windows and Python 3.11/3.14 runs. Local release
verification also builds wheel/sdist, installs the wheel in a clean environment,
reruns tests/demo, checks the console command and pip consistency, and verifies
uninstall. No third-party Python runtime dependencies are used.
