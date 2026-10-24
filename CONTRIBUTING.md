# Contributing

Open an issue with a minimal synthetic reproduction and expected behavior. Never
attach private documents, headers or repository histories. For development:

```sh
python -m pip install -e . ruff==0.17.2 build==1.6.1 coverage==7.17.0
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python -m build
```

Add a regression test for behavioral fixes. Preserve original copyright notices.
Public commits are maintained under the repository owner's Git identity; CI does
not commit changes. Third-party patches require explicit maintainer review and
attribution appropriate to their origin; no automatic PR merging is enabled.
