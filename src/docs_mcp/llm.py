import asyncio
import logging

from docs_mcp.config import settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


async def answer_question(db, query: str) -> dict:
    from docs_mcp.pipeline import embed_and_search

    hits = await embed_and_search(db, query, k=5, mode="hybrid")
    if not hits:
        return {"answer": "No matching documentation found.", "sources": []}
    context_lines = []
    for hit in hits:
        header = hit.title or hit.url
        if hit.heading_path:
            header += " — " + " > ".join(hit.heading_path)
        context_lines.append(f"**{header}**\n\n{hit.content}")
    context = "\n\n".join(context_lines)
    prompt = (
        "Answer the following question using only the provided context. "
        "Keep it concise and conversational.\n\n"
        f"Question: {query}\n\nContext:\n{context}"
    )
    answer = await generate_llm_response(prompt)
    sources: list[dict] = []
    seen_urls: set[str] = set()
    for hit in hits:
        if hit.url in seen_urls:
            continue
        seen_urls.add(hit.url)
        sources.append(
            {
                "title": hit.title or hit.url,
                "url": hit.url,
                "heading_path": hit.heading_path,
                "content": hit.content,
            }
        )
    return {"answer": answer, "sources": sources}


async def generate_llm_response(prompt: str) -> str:
    import httpx

    api_key = settings.llm_api_key
    if not settings.llm_model:
        return "[LLM disabled: set LLM_MODEL in .env]"
    if not api_key:
        return "[LLM disabled: set LLM_API_KEY in .env]"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": settings.llm_max_tokens,
        "temperature": 0.7,
    }

    base_url = settings.llm_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=120.0) as client:
        delay = 3.0
        last_error: Exception | None = None
        for _ in range(MAX_ATTEMPTS):
            try:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                if response.status_code == 429:
                    if "free-models-per-day" in response.text:
                        return (
                            "[LLM provider daily free quota exhausted — "
                            "try again after the daily reset, switch provider "
                            "in .env, or add credits]"
                        )
                    last_error = RuntimeError(
                        f"LLM provider returned 429 (rate limited); retrying"
                    )
                    logger.warning("%s", last_error)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                if response.status_code >= 500:
                    last_error = RuntimeError(
                        f"LLM provider returned {response.status_code}; retrying"
                    )
                    logger.warning("%s", last_error)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                response.raise_for_status()
                result = response.json()
                choice = result.get("choices", [{}])[0]
                content = choice.get("message", {}).get("content", "")
                if choice.get("finish_reason") == "length":
                    content += "\n\n[answer truncated — raise LLM_MAX_TOKENS in .env]"
                return content
            except Exception as exc:
                last_error = exc
                logger.exception("LLM request failed")
                await asyncio.sleep(delay)
                delay *= 2
        return f"[Error generating response: {last_error}]"
