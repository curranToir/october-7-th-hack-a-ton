import httpx

from rag.config import settings
from rag.models import ModelServiceError
from rag.search import Hit


class AnswerNotConfigured(RuntimeError):
    pass


def answer(query: str, hits: list[Hit]) -> str:
    if not settings.answer_base_url or not settings.answer_model:
        raise AnswerNotConfigured(
            "answer endpoint not configured: set ANSWER_BASE_URL/ANSWER_MODEL"
        )

    context = "\n\n".join(
        f"[{number}] ({hit.source}) {hit.content}"
        for number, hit in enumerate(hits, 1)
    )
    messages = [
        {
            "role": "system",
            "content": "Answer using only the provided context. Cite sources as [n]. "
            "If the context is insufficient, say so.",
        },
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
    ]
    headers = (
        {"Authorization": f"Bearer {settings.answer_api_key}"}
        if settings.answer_api_key
        else {}
    )
    url = f"{settings.answer_base_url.rstrip('/')}/chat/completions"
    try:
        response = httpx.post(
            url,
            headers=headers,
            json={"model": settings.answer_model, "messages": messages, "temperature": 0.2},
            timeout=120,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise ModelServiceError(f"answer at {url} failed: {error}") from error
    return response.json()["choices"][0]["message"]["content"]
