"""Shared GPT-5 mini policy; importing this module does not load credentials."""

import json
import os
from collections.abc import Mapping, MutableMapping

MODEL = "gpt-5-mini"
GATEWAY = "https://api.respan.ai/api"
COGNEE_COMPLETION_TOKENS = 32768
ANSWER_COMPLETION_TOKENS = 8192
JUDGE_COMPLETION_TOKENS = 8192


def completion_parameters(
    setting: str, default: int, env: Mapping[str, str] | None = None,
) -> dict:
    """Reasoning and visible output share the bounded completion allowance."""
    values = os.environ if env is None else env
    try:
        budget = int(values.get(setting) or default)
    except (TypeError, ValueError):
        raise ValueError(f"{setting} must be an integer from 1 to 128000") from None
    if not 1 <= budget <= 128000:
        raise ValueError(f"{setting} must be an integer from 1 to 128000")
    return {"model": MODEL, "reasoning_effort": "low", "max_completion_tokens": budget}


def configure_llm_environment(env: MutableMapping[str, str]) -> None:
    """Apply the requested model to all Cognee text stages, without fallback."""
    params = completion_parameters("LLM_MAX_COMPLETION_TOKENS", COGNEE_COMPLETION_TOKENS, env)
    env.update({
        "LLM_MODEL": f"openai/{MODEL}",
        "LLM_PROVIDER": "custom",
        "LLM_ENDPOINT": GATEWAY,
        "JUDGE_MODEL": MODEL,
        "STRUCTURED_OUTPUT_FRAMEWORK": "litellm_native",
        "LLM_MAX_COMPLETION_TOKENS": str(params["max_completion_tokens"]),
        # Cognee 1.6.3 forwards LLM_ARGS to requests; its standalone token setting
        # is also used for chunk sizing and does not alone cap these requests.
        "LLM_ARGS": json.dumps({key: value for key, value in params.items() if key != "model"}),
    })
    for setting in ("LLM_TEMPERATURE", "LLM_SEED"):
        env.pop(setting, None)
    # Empty per-stage values inherit the base model, gateway and Respan key.
    for stage in ("EXTRACTION", "SUMMARIZATION", "QUERY"):
        for field in ("MODEL", "PROVIDER", "ENDPOINT", "API_KEY", "API_VERSION"):
            env[f"LLM_{stage}_{field}"] = ""
    env["IMAGE_TRANSCRIBE_MODEL"] = f"openai/{MODEL}"
    for field in ("MODEL", "ENDPOINT", "API_KEY"):
        env[f"FALLBACK_{field}"] = ""
