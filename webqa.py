# webqa.py
"""
Lightweight website Q&A for RTS:
- Fetches & caches a small set of am2ar.com (mirror of go-rts.com) pages
- Scores pages by keyword overlap
- Sends the best snippets to OpenAI to compose an answer with sources
"""

from __future__ import annotations
import os, re, time
import requests
from bs4 import BeautifulSoup
from typing import List, Tuple

# ---- Config ----
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
BASES = [
    "https://am2ar.com",       # mirror (preferred)
    "https://go-rts.com",      # original
]
HEADERS = {
    "User-Agent": "rts-backend/1.0 (+https://rts-backend)"
}
# Small hand-curated “sitemap”. You can add more later.
PATHS = [
    "/",
    "/fares-and-passes/",
    "/frequently-asked-questions/",
    "/rts-vision-statement-and-mission/",
    "/rts-schedule-disclaimer/",
    "/customer-code-of-conduct/",
    "/contact/",
]

# ---- Simple in-memory cache/index ----
_CACHE: dict[str, dict] = {}   # url -> {"text": "...", "ts": epoch}
_INDEX_BUILT = False
_INDEX_URLS: List[str] = []


def _clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # remove nav/script/style
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fetch(url: str, timeout: float = 10.0) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def _ensure_index() -> None:
    global _INDEX_BUILT, _INDEX_URLS
    if _INDEX_BUILT:
        return
    urls: List[str] = []
    for base in BASES:
        for p in PATHS:
            urls.append(base.rstrip("/") + p)

    # build cache (best-effort; skip failures)
    now = time.time()
    for u in urls:
        try:
            html = _fetch(u)
            txt = _clean_text(html)
            _CACHE[u] = {"text": txt, "ts": now}
        except Exception:
            # ignore unreachable pages
            continue

    # keep only the ones we fetched
    _INDEX_URLS = list(_CACHE.keys())
    _INDEX_BUILT = True


def _tokenize(s: str) -> List[str]:
    s = s.lower()
    return re.findall(r"[a-z0-9]+", s)


def _score(query_tokens: List[str], doc_tokens: List[str]) -> int:
    # simple overlap score
    qset = set(query_tokens)
    dset = set(doc_tokens)
    return len(qset & dset)


def _top_k(query: str, k: int = 3) -> List[Tuple[str, str]]:
    _ensure_index()
    if not _INDEX_URLS:
        return []
    qtok = _tokenize(query)
    scored: List[Tuple[int, str]] = []
    for u in _INDEX_URLS:
        dtok = _tokenize(_CACHE[u]["text"])
        scored.append((_score(qtok, dtok), u))
    # sort by score desc, keep top k with score > 0 (fallback to home if all zero)
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [(u, _CACHE[u]["text"]) for score, u in scored[:k] if score > 0]
    if not top:
        # fallback: include home page (if present)
        for u in _INDEX_URLS:
            if u.endswith("/") and "/fares-and-passes/" not in u:
                top.append((u, _CACHE[u]["text"]))
                break
    return top


def answer(question: str, max_chars_per_doc: int = 1800) -> str:
    """
    Returns a natural-language answer using snippets from am2ar/go-rts.
    """
    from openai import OpenAI
    client = OpenAI()  # uses OPENAI_API_KEY from env

    top_docs = _top_k(question, k=3)
    if not top_docs:
        # Nothing fetched; answer generically.
        prompt = (
            "User question (no site context available):\n"
            f"{question}\n\n"
            "Explain briefly and say you couldn't fetch sources."
        )
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful transit assistant for Gainesville RTS."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()

    # Build context snippets
    context_blocks = []
    used_urls = []
    for url, text in top_docs:
        used_urls.append(url)
        excerpt = text[:max_chars_per_doc]
        context_blocks.append(f"[Source: {url}]\n{excerpt}")

    system = (
        "You are an RTS Gainesville assistant. Answer concisely in the user's language "
        "(English or Spanish). Prefer the provided snippets and cite the sources you used."
    )
    user = (
        "Question:\n"
        f"{question}\n\n"
        "Context from the website (am2ar.com / go-rts.com):\n"
        + "\n\n---\n\n".join(context_blocks)
        + "\n\nInstructions: If the answer is clearly in the snippets, answer directly and then list 'Sources:' "
          "with the URLs you used. If uncertain, say what you know and still provide Sources."
    )

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        temperature=0.2,
    )
    content = resp.choices[0].message.content.strip()

    # Ensure we surface sources (just in case the model forgets).
    if "Sources:" not in content:
        content += "\n\nSources:\n" + "\n".join(f"- {u}" for u in used_urls)

    return content
