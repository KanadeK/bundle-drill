"""Create genuine full/incremental bundles; run successful and missing-base drills."""

import os
import subprocess
import tempfile
from pathlib import Path

from bundle_drill.core import isolated_environment, rehearse


def git_at(path, *args):
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Example",
            "-c",
            "user.email=example@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=" + os.devnull,
            *args,
        ],
        cwd=path,
        env=isolated_environment(),
        check=True,
        capture_output=True,
    )
    return result.stdout.decode().strip()


def create_chain(folder, object_format="sha1"):
    folder = Path(folder)
    source = folder / "source"
    source.mkdir()
    git_at(source, "init", "-b", "main", "--template=", f"--object-format={object_format}")
    (source / "data.txt").write_text("first\n", encoding="utf-8")
    git_at(source, "add", "data.txt")
    git_at(source, "commit", "-m", "first")
    first = git_at(source, "rev-parse", "HEAD")
    base = folder / "base.bundle"
    git_at(source, "bundle", "create", str(base), "--all")
    (source / "data.txt").write_text("second\n", encoding="utf-8")
    git_at(source, "add", "data.txt")
    git_at(source, "commit", "-m", "second")
    tip = git_at(source, "rev-parse", "HEAD")
    git_at(source, "tag", "-a", "v1", "-m", "sample release")
    tag = git_at(source, "rev-parse", "refs/tags/v1")
    update = folder / "update.bundle"
    git_at(source, "bundle", "create", str(update), "--all", "^" + first)
    return base, update, {"refs/heads/main": tip, "refs/tags/v1": tag}


def main():
    with tempfile.TemporaryDirectory() as folder:
        base, update, expected = create_chain(folder)
        report = rehearse([base, update], expected_refs=expected, exact=True)
        missing = rehearse([update])
        if not report["ok"] or missing["ok"]:
            raise RuntimeError("restore demonstration failed")
        print("Full + incremental restore and annotated tag verified; missing base rejected.")
        print("Both scratch repositories cleaned; source repository unchanged by drills.")


if __name__ == "__main__":
    main()
