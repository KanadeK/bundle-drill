import argparse
import json
from pathlib import Path

from . import __version__
from .core import DrillError, rehearse


def _contract(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise DrillError("duplicate contract key")
            result[key] = value
        return result

    with Path(path).open("rb") as stream:
        data = stream.read(1_048_577)
    if len(data) > 1_048_576:
        raise DrillError("contract size limit exceeded")
    return json.loads(data, object_pairs_hook=pairs)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rehearse ordered Git bundle restores offline")
    parser.add_argument("bundles", nargs="+")
    parser.add_argument("--expect", help="JSON object mapping refs to expected object IDs")
    parser.add_argument("--exact", action="store_true")
    parser.add_argument("--max-bytes", type=int, default=1_073_741_824)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    try:
        expected = _contract(args.expect) if args.expect else None
        result = rehearse(
            args.bundles,
            expected_refs=expected,
            exact=args.exact,
            max_bytes=args.max_bytes,
            timeout=args.timeout,
        )
        code = int(not result["ok"])
    except (DrillError, ValueError, OSError) as exc:
        result = {
            "schema": 1,
            "ok": False,
            "error": str(exc)
            if isinstance(exc, DrillError)
            else f"invalid input ({type(exc).__name__})",
        }
        code = 2
    if args.json:
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    else:
        print("Restore drill passed" if code == 0 else "Restore drill failed")
        if "error" in result or "reason" in result:
            print(result.get("error", result.get("reason")))
        for ref, oid in result.get("refs", {}).items():
            print(f"{ascii(ref)} {oid}")
    return code
