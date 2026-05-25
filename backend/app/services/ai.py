import httpx

from app.config import settings


def fallback_summary(text: str, max_chars: int = 480) -> str:
    clean_lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    joined = " ".join(clean_lines)
    if not joined:
        return "暂无摘要。"
    return joined[:max_chars] + ("..." if len(joined) > max_chars else "")


async def generate_summary(text: str, title: str | None = None) -> str:
    if not settings.ai_api_key:
        return fallback_summary(text)
    prompt = (
        "请用中文为以下内容生成一段适合知识库归档和 AI 阅读的摘要。"
        "要求准确、紧凑，不要编造事实，控制在 200 字以内。\n\n"
        f"标题：{title or '未命名'}\n\n内容：\n{text[:12000]}"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post(
                f"{settings.ai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.ai_api_key}"},
                json={
                    "model": settings.ai_model,
                    "messages": [
                        {"role": "system", "content": "你是严谨的内容整理助手。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            payload = response.json()
            return payload["choices"][0]["message"]["content"].strip()
    except Exception:
        return fallback_summary(text)

