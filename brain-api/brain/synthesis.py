"""Grounded answer synthesis through the project's Respan gateway."""

import os

from openai import AsyncOpenAI
from respan import task

from .model_config import ANSWER_COMPLETION_TOKENS, GATEWAY, completion_parameters

ANSWER_PROMPT = (
    "You are Toir Inc's company brain. Answer ONLY from the context blocks below; each block is "
    "labelled with the dataset it came from. Stitch facts across blocks when they refer to the "
    "same client or project. Never attribute facts from one client to another. If the context "
    "does not contain the answer, say what is missing in one sentence. Be concise; end with the "
    "sources used, e.g. (source:slack, source:hubspot)."
)
_llm = None


@task(name="brain.synthesize")
async def synthesize(question, context):
    global _llm
    _llm = _llm or AsyncOpenAI(base_url=GATEWAY, api_key=os.environ["LLM_API_KEY"])
    blocks = "\n\n".join(f"[dataset={item['dataset']}]\n{item['text']}" for item in context)
    result = await _llm.chat.completions.create(
        **completion_parameters("BRAIN_ANSWER_MAX_COMPLETION_TOKENS", ANSWER_COMPLETION_TOKENS),
        messages=[{"role": "system", "content": ANSWER_PROMPT},
                  {"role": "user", "content": f"Context:\n{blocks}\n\nQuestion: {question}"}],
    )
    choice = result.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("brain_answer_completion_limit")
    if not choice.message.content:
        raise RuntimeError("brain_answer_empty")
    return choice.message.content
