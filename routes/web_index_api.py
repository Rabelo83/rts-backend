from flask import Blueprint, jsonify, request
import web_index
import webqa

web_index_bp = Blueprint("web_index", __name__)

@web_index_bp.route("/api/web/ingest", methods=["POST"])
def api_web_ingest():
    body = request.get_json(silent=True) or {}
    base = (body.get("base_url") or web_index.DEFAULT_BASE).strip()
    max_pages = int(body.get("max_pages") or web_index.MAX_PAGES_DEFAULT)
    result = web_index.crawl_and_index(base, max_pages=max_pages)
    return jsonify(result)

@web_index_bp.route("/api/web/ingest_folder", methods=["POST"])
def api_web_ingest_folder():
    body = request.get_json(silent=True) or {}
    folder = (body.get("folder") or "am2ar_mirror").strip()
    result = web_index.ingest_folder(folder)
    return jsonify(result)

@web_index_bp.route("/api/web/search", methods=["GET"])
def api_web_search():
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "q required"}), 400
    hits = web_index.search(q, k=int(request.args.get("k", "5")))
    return jsonify({"hits": hits})

@web_index_bp.route("/api/web/ask", methods=["POST"])
def api_web_ask():
    body = request.get_json(silent=True) or {}
    q = (body.get("question") or "").strip()
    if not q:
        return jsonify({"error": "question is required"}), 400
    ans, src = webqa.answer(q)
    return jsonify({"answer": ans, "sources": src})
