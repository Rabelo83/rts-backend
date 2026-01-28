import os, re, time, json, hashlib, urllib.parse, sqlite3
from collections import deque
from typing import List, Dict, Any, Tuple
import requests
from bs4 import BeautifulSoup
import numpy as np
from openai import OpenAI
import urllib.robotparser as robotparser
from datetime import datetime

# --------- Config ---------
DEFAULT_BASE = "https://53733956.com"
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
USER_AGENT = "RTS-Agent/1.0 (+https://rabelopersonal.com)"
MAX_PAGES_DEFAULT = int(os.getenv("WEB_MAX_PAGES", "200"))
RESPECT_ROBOTS = os.getenv("RESPECT_ROBOTS", "true").lower() != "false"
TIMEOUT = 12

# Where to keep the index on Render:
if os.path.isdir("/data"):
    INDEX_PATH = os.getenv("WEB_INDEX_PATH", "/data/web_index.json")
else:
    INDEX_PATH = os.getenv("WEB_INDEX_PATH", "/tmp/web_index.json")

client = OpenAI()

# In-memory cache (populated by load_index)
_index_entries: List[Dict[str, Any]] = []
_index_matrix: np.ndarray | None = None
_index_dim: int | None = None

def _abs_url(link: str, base: str) -> str | None:
    if not link or link.startswith("#") or link.startswith("mailto:") or link.startswith("tel:") or link.startswith("javascript:"):
        return None
    return urllib.parse.urljoin(base, link)

def _same_host(url: str, root_netloc: str) -> bool:
    try:
        return urllib.parse.urlparse(url).netloc == root_netloc
    except Exception:
        return False

def _fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()

def _chunk(text: str, url: str, max_chars: int = 1600, overlap: int = 150) -> List[Dict[str, str]]:
    out = []
    i = 0
    n = len(text)
    while i < n:
        out.append({"url": url, "text": text[i:i+max_chars]})
        i += max_chars - overlap
    return out

def _embed_texts(texts: List[str]) -> List[List[float]]:
    res = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in res.data]

def crawl_and_index(base_url: str = DEFAULT_BASE, max_pages: int = MAX_PAGES_DEFAULT) -> Dict[str, Any]:
    """Crawl within the same host, build chunk embeddings, and save JSON index."""
    parsed = urllib.parse.urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    root_netloc = parsed.netloc

    if RESPECT_ROBOTS:
        rp = robotparser.RobotFileParser()
        try:
            rp.set_url(urllib.parse.urljoin(root, "/robots.txt"))
            rp.read()
        except Exception:
            rp = None
    else:
        rp = None

    frontier = deque([base_url])
    seen = set([base_url])
    pages: List[Tuple[str, str]] = []  # (url, text)

    while frontier and len(pages) < max_pages:
        url = frontier.popleft()
        try:
            if rp and not rp.can_fetch(USER_AGENT, url):
                continue
            html = _fetch_html(url)
            text = _html_to_text(html)
            if text:
                pages.append((url, text))
            # find links
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                absu = _abs_url(a["href"], url)
                if not absu:
                    continue
                if not _same_host(absu, root_netloc):
                    continue
                # normalize (drop fragments/query for crawl identity)
                u = urllib.parse.urlparse(absu)
                norm = urllib.parse.urlunparse((u.scheme, u.netloc, u.path, "", "", ""))
                if norm not in seen:
                    seen.add(norm)
                    frontier.append(norm)
            time.sleep(0.15)  # be polite
        except Exception:
            continue

    # chunk & embed
    chunks = []
    for url, text in pages:
        chunks.extend(_chunk(text, url))
    embeddings = _embed_texts([c["text"] for c in chunks])

    entries = []
    for c, e in zip(chunks, embeddings):
        entries.append({"url": c["url"], "text": c["text"], "embedding": e})

    meta = {
        "model": EMBED_MODEL,
        "built_at": datetime.utcnow().isoformat() + "Z",
        "base_url": base_url,
        "count": len(entries),
    }
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "entries": entries}, f)

    # refresh in-memory
    load_index()
    return {"ok": True, "saved": INDEX_PATH, "pages": len(pages), "chunks": len(entries), "base_url": base_url}

def ingest_folder(folder_path: str) -> Dict[str, Any]:
    """Index a local mirror folder (commit it into the repo) containing .html files."""
    html_files: List[str] = []
    for root, _, files in os.walk(folder_path):
        for name in files:
            if name.lower().endswith((".html", ".htm")):
                html_files.append(os.path.join(root, name))

    chunks = []
    for fp in html_files:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                html = f.read()
            text = _html_to_text(html)
            if not text:
                continue
            # fabricate a file URL (so you still see where it came from)
            url = "file://" + os.path.abspath(fp)
            chunks.extend(_chunk(text, url))
        except Exception:
            continue

    if not chunks:
        return {"ok": False, "error": "no html files or empty"}

    embeddings = _embed_texts([c["text"] for c in chunks])
    entries = [{"url": c["url"], "text": c["text"], "embedding": e} for c, e in zip(chunks, embeddings)]

    meta = {
        "model": EMBED_MODEL,
        "built_at": datetime.utcnow().isoformat() + "Z",
        "base_url": f"folder:{folder_path}",
        "count": len(entries),
    }
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "entries": entries}, f)

    load_index()
    return {"ok": True, "saved": INDEX_PATH, "chunks": len(entries)}

def load_index() -> Dict[str, Any]:
    global _index_entries, _index_matrix, _index_dim
    if not os.path.exists(INDEX_PATH):
        _index_entries, _index_matrix, _index_dim = [], None, None
        return {"ok": False, "loaded": 0}
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    _index_entries = data.get("entries", [])
    if not _index_entries:
        _index_matrix, _index_dim = None, None
        return {"ok": True, "loaded": 0}
    M = np.array([e["embedding"] for e in _index_entries], dtype="float32")
    # cosine: normalize rows
    norms = np.linalg.norm(M, axis=1, keepdims=True) + 1e-9
    _index_matrix = M / norms
    _index_dim = _index_matrix.shape[1]
    return {"ok": True, "loaded": len(_index_entries)}

def search(query: str, k: int = 5) -> List[Dict[str, Any]]:
    if _index_matrix is None or not _index_entries:
        load_index()
    if _index_matrix is None or not _index_entries:
        return []
    qv = client.embeddings.create(model=EMBED_MODEL, input=[query]).data[0].embedding
    q = np.array(qv, dtype="float32")
    q = q / (np.linalg.norm(q) + 1e-9)
    sims = _index_matrix @ q
    idx = np.argsort(-sims)[:k]
    out = []
    for i in idx:
        e = _index_entries[int(i)]
        out.append({"url": e["url"], "text": e["text"], "score": float(sims[int(i)])})
    return out
