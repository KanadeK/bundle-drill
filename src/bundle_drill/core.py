"""Rehearse local Git bundles in disposable, configuration-isolated repositories."""

import hashlib
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path


class DrillError(ValueError):
    """An input is invalid, unsupported, or could not be fully checked."""


class GitFailure(Exception):
    """Git rejected a stage of the restoration."""


def isolated_environment():
    """Do not inherit repository selection, injected config, alternates or hooks."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_OPTIONAL_LOCKS="0",
        LC_ALL="C",
        GIT_ALLOW_PROTOCOL="",
        GIT_ATTR_NOSYSTEM="1",
    )
    return env


def _git(git, cwd, env, deadline, *args, data=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DrillError("drill time limit exceeded")
    command = [
        git,
        "--no-pager",
        "-c",
        "core.hooksPath=" + os.devnull,
        "-c",
        "gc.auto=0",
        "-c",
        "maintenance.auto=false",
        "-c",
        "protocol.allow=never",
        *args,
    ]
    # Files avoid unbounded Python RAM use if Git emits many diagnostics.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=out,
            stderr=err,
            stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        try:
            proc.communicate(data, timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise DrillError("drill time limit exceeded") from exc
        if proc.returncode:
            # Raw Git errors can include private commit messages and local paths.
            raise GitFailure(f"Git {args[0]} rejected the input")
        out.seek(0)
        result = out.read(1_048_577)
        if len(result) > 1_048_576:
            raise DrillError("Git output limit exceeded")
        return result


def _header(path):
    with path.open("rb") as stream:
        magic = stream.readline(8193)
        if magic not in (b"# v2 git bundle\n", b"# v3 git bundle\n"):
            raise DrillError("unsupported Git bundle header")
        object_format = "sha1"
        refs = {}
        count = len(magic)
        while True:
            line = stream.readline(8193)
            count += len(line)
            if not line or len(line) > 8192 or count > 1_048_576:
                raise DrillError("invalid or oversized Git bundle header")
            if line == b"\n":
                break
            if line.startswith(b"@"):
                if line == b"@object-format=sha256\n":
                    object_format = "sha256"
                elif line != b"@object-format=sha1\n":
                    raise DrillError("filtered or unknown bundle capability unsupported")
                continue
            if line.startswith(b"-"):
                continue  # Prerequisites are verified by Git, without exposing subjects.
            try:
                oid, ref = line.rstrip(b"\n").decode("utf-8").split(" ", 1)
            except (UnicodeError, ValueError) as exc:
                raise DrillError("invalid bundle reference line") from exc
            if (
                not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", oid)
                or ref in refs
                or any(c.isspace() or ord(c) < 32 for c in ref)
                or (ref != "HEAD" and not ref.startswith("refs/"))
            ):
                raise DrillError("invalid or duplicate bundle reference")
            refs[ref] = oid
            if len(refs) > 4096:
                raise DrillError("bundle reference limit exceeded")
        if not any(ref.startswith("refs/") for ref in refs):
            raise DrillError("bundle requires named refs; HEAD-only bundles are unsupported")
        if any(len(oid) != (64 if object_format == "sha256" else 40) for oid in refs.values()):
            raise DrillError("object format and reference length disagree")
        return object_format, refs


def rehearse(
    paths,
    *,
    expected_refs=None,
    exact=False,
    max_bytes=1_073_741_824,
    timeout=120,
    scratch_parent=None,
):
    """Return a receipt for an ordered full/incremental bundle chain.

    Only scratch state is modified. expected_refs is an optional ref->object-id
    mapping; exact additionally rejects unexpected restored refs. HEAD is an alias,
    not a persistent ref. A failed restoration returns ok=False; invalid input raises.
    """
    paths = list(paths)
    if not paths or len(paths) > 128 or max_bytes < 1 or not math.isfinite(timeout) or timeout <= 0:
        raise DrillError("provide 1..128 bundles and positive limits")
    if exact and expected_refs is None:
        raise DrillError("exact mode requires an expected-ref contract")
    if expected_refs is not None:
        if not isinstance(expected_refs, dict) or not expected_refs:
            raise DrillError("expected_refs must be a nonempty object")
        for key, value in expected_refs.items():
            if (
                not isinstance(key, str)
                or not key.startswith("refs/")
                or not isinstance(value, str)
                or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value)
            ):
                raise DrillError("invalid expected-ref contract")
    git = shutil.which("git")
    if not git:
        raise DrillError("Git executable is required on PATH")
    deadline = time.monotonic() + timeout
    env = isolated_environment()
    receipts = []
    total = 0
    try:
        with tempfile.TemporaryDirectory(prefix="bundle-drill-", dir=scratch_parent) as folder:
            scratch = Path(folder)
            copies = []
            for index, original in enumerate(paths):
                copy = scratch / f"input-{index}.bundle"
                digest = hashlib.sha256()
                with Path(original).open("rb") as source, copy.open("xb") as dest:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise DrillError("bundles must be regular local files")
                    while block := source.read(1_048_576):
                        total += len(block)
                        if total > max_bytes:
                            raise DrillError("total bundle byte limit exceeded")
                        if time.monotonic() > deadline:
                            raise DrillError("drill time limit exceeded")
                        digest.update(block)
                        dest.write(block)
                fmt, refs = _header(copy)
                copies.append((copy, fmt, refs))
                receipts.append(
                    {
                        "index": index,
                        "sha256": digest.hexdigest(),
                        "bytes": copy.stat().st_size,
                        "verified": False,
                    }
                )
            object_format = copies[0][1]
            if any(fmt != object_format for _, fmt, _ in copies):
                raise DrillError("mixed Git object formats are unsupported")
            if len({ref for _, _, refs in copies for ref in refs}) > 4096:
                raise DrillError("total bundle reference limit exceeded")
            bare = scratch / "restore.git"
            _git(
                git,
                scratch,
                env,
                deadline,
                "init",
                "--bare",
                "--template=",
                f"--object-format={object_format}",
                str(bare),
            )
            for index, (copy, _, refs) in enumerate(copies):
                try:
                    _git(git, bare, env, deadline, "bundle", "verify", str(copy))
                    _git(git, bare, env, deadline, "bundle", "unbundle", str(copy))
                    updates = "".join(
                        f"update {ref} {oid}\n" for ref, oid in refs.items() if ref != "HEAD"
                    )
                    if updates:
                        _git(
                            git,
                            bare,
                            env,
                            deadline,
                            "update-ref",
                            "--stdin",
                            data=updates.encode("utf-8"),
                        )
                    receipts[index]["verified"] = True
                except GitFailure as exc:
                    return {
                        "schema": 1,
                        "ok": False,
                        "stage": index,
                        "reason": str(exc),
                        "bundles": receipts,
                        "refs": {},
                    }
            try:
                _git(git, bare, env, deadline, "fsck", "--full", "--strict", "--no-reflogs")
            except GitFailure as exc:
                return {
                    "schema": 1,
                    "ok": False,
                    "reason": str(exc),
                    "bundles": receipts,
                    "refs": {},
                }
            raw = _git(
                git, bare, env, deadline, "for-each-ref", "--format=%(refname) %(objectname)"
            )
            restored = dict(line.split(" ", 1) for line in raw.decode("utf-8").splitlines())
            # for-each-ref returns ref->oid. A later bundle updates existing refs;
            # absent refs are preserved because bundle format has no deletion log.
            mismatches = sorted(k for k, v in (expected_refs or {}).items() if restored.get(k) != v)
            unexpected = sorted(set(restored) - set(expected_refs or {})) if exact else []
            return {
                "schema": 1,
                "ok": not mismatches and not unexpected,
                "object_format": object_format,
                "bundles": receipts,
                "refs": restored,
                "mismatched_refs": mismatches,
                "unexpected_refs": unexpected,
                "fsck": "passed",
            }
    except (OSError, UnicodeError, GitFailure) as exc:
        raise DrillError(f"cannot complete drill ({type(exc).__name__})") from exc
