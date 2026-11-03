# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Decision tracing shared by every ported module (a no-op unless a DecisionTrace is installed).

The parity tests install a trace, run the port, and report which comparisons against a numeric threshold came
close to flipping and which branches ran. A mismatch next to a near-threshold decision is investigated, never
tolerated (the parity rule of the project).
"""

_trace = None
_ctx = (None, None)          # (item id, index) of the thing being computed, set by the callers that know it


class DecisionTrace:
    def __init__(self):
        self.decisions = []      # (name, value, threshold, item id, index)
        self.branches = {}       # branch name -> count
        self.timings = {}        # label -> seconds (filled by the tests)

    def decision(self, name, value, threshold):
        self.decisions.append((name, value, threshold, _ctx[0], _ctx[1]))

    def branch(self, name):
        self.branches[name] = self.branches.get(name, 0) + 1

    def near(self, rel=1e-6):
        """decisions whose value sits within `rel` (relative to the threshold; absolute when it is 0) of the threshold"""
        out = []
        for name, v, thr, cid, i in self.decisions:
            if v != v or thr != thr:
                continue
            if thr in (float('inf'), float('-inf')) or v in (float('inf'), float('-inf')):
                continue
            margin = abs(v - thr) / abs(thr) if thr != 0 else abs(v - thr)
            if margin < rel:
                out.append((name, v, thr, margin, cid, i))
        return out


def set_trace(trace):
    global _trace
    _trace = trace


def get_trace():
    return _trace


def set_ctx(item_id, index=None):
    """Sets the current context and returns the previous one (restore it with set_ctx(*previous))."""
    global _ctx
    prev = _ctx
    _ctx = (item_id, index)
    return prev


def dec(name, value, threshold):
    """Records `value` compared against `threshold` and returns `value` unchanged, so it can sit inside the `if`."""
    if _trace is not None:
        _trace.decision(name, value, threshold)
    return value


def br(name):
    if _trace is not None:
        _trace.branch(name)


def dec_near(name, value, threshold, rel=1e-6):
    """Like dec(), for hot loops (millions of tests): records the comparison only when it is already close to the
    threshold, so the trace stays small while every near-miss is still reported."""
    if _trace is not None and value == value:
        margin = abs(value - threshold) / abs(threshold) if threshold != 0 else abs(value - threshold)
        if margin < rel:
            _trace.decision(name, value, threshold)
    return value
