"""Course module: an isolated STEM course workbench domain (PDR-003).

Layout:
- models.py       — ObjectModel subclasses for the migration 24 + 25 Course schema
- state_machine.py — the transition contracts every state change must pass
- locking.py      — domain-scoped serialization for heavy course jobs
"""

from open_notebook.course import locking, models, state_machine  # noqa: F401
