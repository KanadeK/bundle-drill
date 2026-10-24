# Bundle Drill

Prove that your ordered Git bundle backups can restore without the original repository.

An incremental `.bundle` can verify successfully in your working repository because
the required older commits are already there. That does not prove the files on your
backup disk are sufficient. Bundle Drill starts with an empty temporary bare
repository, verifies and imports every bundle in order, restores advertised refs,
and runs `git fsck --full --strict`. The scratch repository is then removed.

## Install

Requires Python 3.11+ and a current Git on PATH (Git 2.29+ supports the object formats
used here). No third-party Python runtime dependencies, account or network service.

```sh
git clone https://github.com/KanadeK/bundle-drill.git
cd bundle-drill
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install .
python examples/demo.py
bundle-drill /backups/base.bundle /backups/update-01.bundle --json
```

The demo creates real full/incremental bundles and an annotated tag. It proves a
successful restore and rejects the incremental file when its base is omitted.
Everything is synthetic and all temporary files are cleaned up.

Exit codes: **0** restore and optional contract passed; **1** Git rejected a restore
stage, fsck failed, or refs differ from the contract; **2** invalid/unsupported input,
missing Git, resource limit or I/O failure. With `--json`, stdout is one JSON object.
Reports identify bundles by input index, SHA-256 and size, not private filenames or
commit subjects. Ref names and object IDs remain visible; review before sharing.

## Expected-ref contracts

Capture the refs you intend to preserve at backup time in a JSON object. For example:

```json
{"refs/heads/main":"0123456789012345678901234567890123456789"}
```

The example object ID is illustrative; use your real `git rev-parse refs/heads/main`
value. Then run:

```sh
bundle-drill base.bundle update.bundle --expect expected-refs.json --exact --json
```

Without `--exact`, the listed refs must match and additional refs are allowed. With
`--exact`, every restored named ref must appear in the contract. For annotated tags,
record the tag object ID (`git rev-parse refs/tags/v1`), not its peeled commit ID.
Duplicate contract keys are rejected. SHA-1 and SHA-256 bundles are supported;
mixing formats within one chain is rejected.

## What the proof covers

- Copies each input into private scratch storage while hashing; subsequent Git
  operations read those copies. The original bundles/repository are never modified.
- Uses a new bare repository, no checkout, no hooks, no inherited Git configuration,
  alternates, replacement objects, credentials or remote fetches.
- Restores valid advertised named refs. Later bundles update earlier refs; refs
  absent from a later bundle persist. **Bundles do not record ref deletion.** Use
  an exact expected-ref contract to detect stale refs in your backup chain.
- Uses Git's own bundle and object validators, then verifies final object integrity
  with fsck. It proves structural recoverability, not authorship or trusted origin.
- Does not restore LFS object contents, submodule repositories, working-tree files,
  ignored/untracked data, reflogs, hooks or repository configuration. Back those up
  separately. `HEAD` aliases are not installed; create bundles with named refs
  (for example `git bundle create base.bundle --all`). HEAD-only and filtered
  bundles are deliberately unsupported in 0.1.0.

## Resource limits and cleanup

Defaults: 128 bundles, 1 GiB total compressed input (`--max-bytes`), 120 seconds
overall (`--timeout`), 4096 distinct advertised refs, 1 MiB header and 8192 bytes per
header line. Timeouts terminate the Git process group/tree. Scratch storage is
removed on success and ordinary failures. Abrupt OS/process termination can leave
a system-temp directory with prefix `bundle-drill-`; remove it after confirming no
drill is running.

Git pack expansion and diagnostic files consume disk beyond compressed input size;
there is **no hard decompressed-disk quota**. Use your own backups on a volume with
adequate space. The tool is not a sandbox for hostile Git objects. Ref names must
be UTF-8. Per-stage failure reports avoid raw Git output that could disclose private
commit subjects or paths. The tool sends no telemetry and writes reports only to stdout.

Uninstall with `python -m pip uninstall bundle-drill`. Remove the dedicated virtual
environment and checkout if no longer needed. No service or persistent cache is created.

## Why another bundle utility

[`git bundle verify`](https://git-scm.com/docs/git-bundle) tests prerequisites against
the current repository. [git-backup](https://github.com/bocytko/git-backup) creates
and restores incremental backups with shell scripts. Bundle Drill is a repeatable,
cross-platform rehearsal from zero with ordered receipts and final ref contracts.
It uses Git rather than reimplementing pack parsing. No competitor code is included.

MIT. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and
[VERIFICATION.md](VERIFICATION.md). Version **0.1.0**.

## 中文说明

在临时空白裸仓库中，按顺序导入完整备份和增量 Git bundle，逐个检查前置提交，
恢复命名引用并执行 fsck。它能发现“在源仓库验证通过，但离开源仓库无法恢复”
的问题。支持期望引用 JSON 合约与严格匹配；不联网、不修改原始备份。
不包含 LFS 内容、工作区文件或子模块仓库，也不能从 bundle 推断引用删除。
运行 `python examples/demo.py` 可演练成功恢复和缺失基础备份两种场景。
