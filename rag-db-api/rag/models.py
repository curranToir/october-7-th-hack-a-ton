import httpx

from rag.config import settings

EMBED_DIM = 2048


class ModelServiceError(RuntimeError):
    pass


def _embed(inputs: list[str]) -> list[list[float]]:
    if not inputs:
        return []
    url = f"{settings.embed_base_url.rstrip('/')}/embeddings"
    vectors = []
    try:
        with httpx.Client(timeout=120.0) as client:
            for start in range(0, len(inputs), 32):
                batch = inputs[start : start + 32]
                response = client.post(url, json={"model": settings.embed_model, "input": batch})
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda item: item["index"])
                assert [item["index"] for item in data] == list(range(len(batch))), "embedding batch mismatch"
                for item in data:
                    vector = item["embedding"]
                    assert len(vector) == EMBED_DIM, f"expected {EMBED_DIM} embedding dimensions"
                    vectors.append(vector)
    except httpx.HTTPError as err:
        raise ModelServiceError(f"embedder at {url} failed: {err}") from err
    return vectors


def embed_passages(texts: list[str]) -> list[list[float]]:
    return _embed([f"passage: {text}" for text in texts])


def embed_query(text: str) -> list[float]:
    return _embed([f"query: {text}"])[0]


def rerank(query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    if not documents:
        return []
    url = f"{settings.rerank_base_url.rstrip('/')}/rerank"
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                url,
                json={
                    "model": settings.rerank_model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                },
            )
            response.raise_for_status()
            results = [(item["index"], float(item["relevance_score"])) for item in response.json()["results"]]
    except httpx.HTTPError as err:
        raise ModelServiceError(f"reranker at {url} failed: {err}") from err
    if any(index < 0 or index >= len(documents) for index, _ in results):
        raise ModelServiceError(f"reranker at {url} returned an invalid document index")
    return sorted(results, key=lambda item: (-item[1], item[0]))
