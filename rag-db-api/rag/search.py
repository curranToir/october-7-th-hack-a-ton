from dataclasses import dataclass

from pgvector import HalfVector
from psycopg import sql

from rag.models import embed_query, rerank


@dataclass
class Hit:
    chunk_id: int
    document_id: int
    source: str
    title: str | None
    ord: int
    content: str
    metadata: dict
    score: float
    vector_rank: int | None
    text_rank: int | None


def rrf(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    if k < 0:
        raise ValueError("k must be nonnegative")
    scores = {}
    for ranking in rankings:
        seen = set()
        for rank, chunk_id in enumerate(ranking, 1):
            if chunk_id not in seen:
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
                seen.add(chunk_id)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def search(conn, query: str, top_k: int = 5, candidates: int = 50, use_rerank: bool = True) -> list[Hit]:
    if top_k <= 0 or candidates <= 0:
        raise ValueError("top_k and candidates must be positive")
    embedding = HalfVector(embed_query(query))
    with conn.transaction():
        conn.execute(sql.SQL("SET LOCAL hnsw.ef_search = {}").format(sql.Literal(max(candidates, 40))))
        vector_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM chunks ORDER BY embedding <=> %s LIMIT %s", (embedding, candidates)
            ).fetchall()
        ]
        text_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM chunks WHERE tsv @@ websearch_to_tsquery('english', %s) "
                "ORDER BY ts_rank_cd(tsv, websearch_to_tsquery('english', %s)) DESC LIMIT %s",
                (query, query, candidates),
            ).fetchall()
        ]
        fused = rrf([vector_ids, text_ids])[:candidates]
        if not fused:
            return []
        rows = conn.execute(
            "SELECT c.id AS chunk_id, c.document_id, d.source, d.title, c.ord, c.content, c.metadata "
            "FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id = ANY(%s)",
            ([chunk_id for chunk_id, _ in fused],),
        ).fetchall()
    by_id = {row["chunk_id"]: row for row in rows}
    fused = [(chunk_id, score) for chunk_id, score in fused if chunk_id in by_id]
    if not fused:
        return []
    if use_rerank:
        selected = fused[:min(candidates, 30)]
        ranked = [
            (selected[index][0], score)
            for index, score in rerank(
                query, [by_id[chunk_id]["content"] for chunk_id, _ in selected], min(top_k, len(selected))
            )
        ]
    else:
        ranked = fused
    vector_ranks = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids, 1)}
    text_ranks = {chunk_id: rank for rank, chunk_id in enumerate(text_ids, 1)}
    return [
        Hit(
            **by_id[chunk_id],
            score=score,
            vector_rank=vector_ranks.get(chunk_id),
            text_rank=text_ranks.get(chunk_id),
        )
        for chunk_id, score in ranked[:top_k]
    ]
