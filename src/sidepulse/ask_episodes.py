"""Batching for ask announcements: rapid approval chains announce once.

tlip's permission-batch window, adapted to the glance episode model: a
NEW ask arriving within the batch window of the previous ask's
announcement reuses that episode key, so the finite arrival taps and
chime play once for the burst instead of once per approval. The window
is anchored at the FIRST ask of the burst -- a long chain cannot extend
it forever -- and an ask that stays pending keeps its own key, so
nothing here ever hides or shortens a real ask; it only merges the
back-to-back re-announcements.
"""

from __future__ import annotations

ASK_BATCH_SECONDS = 2.0

_BUCKET_ATTR = "_ask_episode_bucket"


def batched_episode_key(controller, key: str, now: float) -> str:
    previous = getattr(controller, _BUCKET_ATTR, None)
    if previous is not None and previous[0] == key:
        return key
    if previous is not None and now - previous[1] < ASK_BATCH_SECONDS:
        return previous[0]
    setattr(controller, _BUCKET_ATTR, (key, now))
    return key


__all__ = ["ASK_BATCH_SECONDS", "batched_episode_key"]
