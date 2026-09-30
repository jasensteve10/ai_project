"""Bounded, expiring success-only cache; instantiate separately per session."""
import copy
import time
from collections import OrderedDict


class ResultCache:
    def __init__(self, ttl=300, max_entries=128, clock=time.monotonic):
        self.ttl, self.max_entries, self.clock = ttl, max_entries, clock
        self.entries = OrderedDict()

    def query(self, question, version, run):
        key = (version, question)
        now = self.clock()
        for old_key, (created, _) in list(self.entries.items()):
            if now - created >= self.ttl:
                del self.entries[old_key]
        if key in self.entries:
            self.entries.move_to_end(key)
            return {**copy.deepcopy(self.entries[key][1]), 'cache_hit': True}
        result = run(question)
        if result.get('ok') and result.get('status') == 'answerable':
            self.entries[key] = (self.clock(), copy.deepcopy(result))
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)
        return {**result, 'cache_hit': False}

    def clear(self):
        self.entries.clear()
