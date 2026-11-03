import uuid

import pytest

from app.retrieval.search import rrf_fuse

A, B, C = (uuid.uuid4() for _ in range(3))


def test_item_first_in_both_beats_item_first_in_one() -> None:
    fused = rrf_fuse([[A, B], [A, C]])
    assert [i for i, _ in fused] == [A, B, C]
    assert fused[0][1] == pytest.approx(2 / 61)


def test_ties_keep_first_seen_order() -> None:
    assert [i for i, _ in rrf_fuse([[A], [B]])] == [A, B]
    assert [i for i, _ in rrf_fuse([[B], [A]])] == [B, A]


def test_empty_inputs() -> None:
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []
