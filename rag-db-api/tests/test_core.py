import pytest

from rag.chunk import chunk_text
from rag.search import rrf


@pytest.mark.parametrize("text", ["", " \t\n\r\n "])
def test_chunk_empty(text):
    assert chunk_text(text) == []


def test_chunk_normalizes_and_packs_paragraphs():
    text = "  Alpha paragraph.\r\n\r\nBeta paragraph.\r\n\r\nGamma paragraph.  "
    chunks = chunk_text(text, max_chars=35, overlap=5)
    assert chunks[0] == "Alpha paragraph.\n\nBeta paragraph."
    assert len(chunks) == 2
    assert chunks[1].startswith(chunks[0][-5:])
    assert chunks[1].endswith("Gamma paragraph.")
    assert all(0 < len(chunk) <= 35 for chunk in chunks)


def test_chunk_long_paragraph_uses_sentences_then_hard_splits():
    text = "First short sentence. " + "x" * 120 + ". Last short sentence."
    chunks = chunk_text(text, max_chars=50, overlap=8)
    assert chunks[0] == "First short sentence."
    assert len(chunks) > 3
    assert all(0 < len(chunk) <= 50 for chunk in chunks)
    assert chunks[-1].endswith("Last short sentence.")


def test_chunk_hard_split_retains_all_text_and_overlap():
    text = "abcdefghij" * 40
    chunks = chunk_text(text, max_chars=64, overlap=7)
    assert all(0 < len(chunk) <= 64 for chunk in chunks)
    assert all(right.startswith(left[-7:]) for left, right in zip(chunks, chunks[1:]))
    assert chunks[0] + "".join(chunk[7:] for chunk in chunks[1:]) == text


def test_chunk_zero_overlap():
    assert chunk_text("abcdefghij", max_chars=4, overlap=0) == ["abcd", "efgh", "ij"]


@pytest.mark.parametrize("max_chars, overlap", [(0, 0), (5, -1), (5, 5)])
def test_chunk_rejects_invalid_bounds(max_chars, overlap):
    with pytest.raises(ValueError):
        chunk_text("text", max_chars=max_chars, overlap=overlap)


def test_rrf_rewards_shared_matches():
    results = rrf([[1, 3], [2, 3]])
    assert results[0][0] == 3
    assert results[0][1] == pytest.approx(2 / 62)
    assert [chunk_id for chunk_id, _ in results[1:]] == [1, 2]


def test_rrf_ties_are_deterministic():
    assert [chunk_id for chunk_id, _ in rrf([[2, 1], [1, 2]])] == [1, 2]
    assert rrf([[2, 1], [1, 2]]) == rrf([[1, 2], [2, 1]])


def test_rrf_empty_and_duplicate_rankings():
    assert rrf([]) == []
    assert rrf([[], []]) == []
    assert rrf([[1, 1]]) == [(1, 1 / 61)]
    assert rrf([[9]], k=0) == [(9, 1.0)]


def test_rrf_rejects_negative_constant():
    with pytest.raises(ValueError):
        rrf([[1]], k=-1)
