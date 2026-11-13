"""Resident component observations; serialized evidence never creates a handle."""
from copy import deepcopy
import os

from .state import StateError, _read, _sha


class ComponentObservations:
    def __init__(self):
        self._pid, self._items = os.getpid(), []

    def retain(self, outcome, report):
        pins = tuple((path, _sha(_read(path, 2_000_000))) for path in outcome.artifacts)
        self._items.append((outcome, deepcopy(report), pins))
        return outcome

    def observe(self, outcome):
        if self._pid != os.getpid():
            raise StateError('Component observation belongs to another process')
        for original, report, pins in self._items:
            if outcome is original:
                if any(_sha(_read(path, 2_000_000)) != digest for path, digest in pins):
                    raise StateError('Component observation evidence changed')
                return deepcopy(report)
        raise StateError('Only an original resident component outcome may be observed')
