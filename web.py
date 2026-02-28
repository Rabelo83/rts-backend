import os
from openai import OpenAI
import web_index

_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=_OPENAI_KEY) if _OPENAI_KEY else None
CHAT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def answer(question: str, k: int = 5) -> tuple[str, list[str]]:
    if client is None:
        return ("The OpenAI client is not configured in this environment.", [])
    hits = web_index.search(question, k=k)
    if not hits:
        return ("I don’t have a local index yet. Please run the ingest endpoint first.", [])

    # Build context (trim per chunk to keep prompt small)
    ctx_parts = []
    for h in hits:
        ctx_parts.append(f"[source: {h['url']}]\n{h['text'][:1200]}")
    context = "\n\n".join(ctx_parts)

    sys = (
        "You are an RTS Gainesville helper. Answer ONLY using the provided "
        "context (mirrored from am2ar.com). If the answer is not present, say "
        "you don’t know. Keep answers concise and include one best source URL."
    )

    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        temperature=0.1,
        max_tokens=300,
        messages=[
            {"role":"system","content":sys},
            {"role":"user","content": f"QUESTION:\n{question}\n\nCONTEXT:\n{context}"}
        ],
    )
    ans = resp.choices[0].message.content.strip()
    sources = list(dict.fromkeys([h["url"] for h in hits]))
    return ans, sources
