import os
import sys
from pathlib import Path
from flask import Flask, send_from_directory, request, redirect, session, render_template_string
from flask_cors import CORS

sys.path.insert(0, str(Path(__file__).resolve().parent / "utils"))
from limiter import limiter  # noqa: E402

# Blueprints (your split routes)
from routes.health import health_bp
from routes.bustime import bustime_bp
from routes.agent_api import bp as agent_bp
from routes.schedule_api import schedule_bp
from routes.project_status import project_status_bp
from routes.admin_api import admin_bp
from routes.trip_api import trip_bp

# If you have web index routes, keep this import.
try:
    from routes.web_index_api import web_index_bp
except Exception:
    web_index_bp = None

_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>RTS Access</title>
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{background:#070d1a;display:flex;align-items:center;justify-content:center;
         min-height:100vh;font-family:"Segoe UI",sans-serif;color:#e6edf3}
    .card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);
          border-radius:16px;padding:40px 36px;width:100%;max-width:360px;
          backdrop-filter:blur(20px);text-align:center}
    h1{font-size:1.25rem;margin-bottom:6px;color:#fff}
    p{font-size:.85rem;color:rgba(255,255,255,.45);margin-bottom:28px}
    input{width:100%;padding:12px 16px;background:rgba(255,255,255,.06);
          border:1px solid rgba(255,255,255,.15);border-radius:10px;color:#fff;
          font-size:1.1rem;text-align:center;letter-spacing:.2em;outline:none;
          font-family:inherit;transition:border-color .2s}
    input:focus{border-color:#58a6ff}
    button{margin-top:16px;width:100%;padding:13px;background:linear-gradient(135deg,#2563eb,#7c3aed);
           border:none;border-radius:10px;color:#fff;font-size:.95rem;font-weight:600;
           cursor:pointer;transition:opacity .2s}
    button:hover{opacity:.88}
    .err{margin-top:14px;color:#f85149;font-size:.85rem}
  </style>
</head>
<body>
  <div class="card">
    <h1>RTS Assistant</h1>
    <p>Enter your access PIN to continue</p>
    <form method="POST" action="/login">
      <input type="hidden" name="next" value="{{ next }}"/>
      <input type="password" name="pin" placeholder="••••" autofocus autocomplete="off"/>
      <button type="submit">Continue</button>
      {% if error %}<div class="err">Incorrect PIN — try again</div>{% endif %}
    </form>
  </div>
</body>
</html>"""


def create_app() -> Flask:
    app = Flask(__name__, static_folder="public_html", static_url_path="/static")
    app.secret_key = os.environ.get("SECRET_KEY", "rts-dashboard-dev-key-change-in-prod")

    _origins = os.environ.get("CORS_ORIGINS", "*")
    CORS(app, origins=_origins.split(",") if _origins != "*" else "*")
    limiter.init_app(app)

    # Register routes
    app.register_blueprint(health_bp)
    app.register_blueprint(bustime_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(schedule_bp)
    app.register_blueprint(project_status_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(trip_bp)

    # Pre-load stop geo index
    try:
        from utils.stop_finder import ensure_stops_db
        ensure_stops_db()
    except Exception:
        pass

    if web_index_bp:
        app.register_blueprint(web_index_bp)

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # ── Shared PIN auth helpers ──────────────────────────────────
    def _pin_required() -> bool:
        return bool(os.environ.get("DASHBOARD_PIN", "").strip())

    def _pin_ok() -> bool:
        return session.get("dashboard_auth") is True

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not _pin_required():
            return redirect(request.args.get("next") or "/")
        next_url = request.args.get("next", "/")
        error = False
        if request.method == "POST":
            next_url = request.form.get("next", "/")
            if request.form.get("pin", "") == os.environ.get("DASHBOARD_PIN", ""):
                session["dashboard_auth"] = True
                # only allow relative redirects for safety
                if next_url.startswith("/") and not next_url.startswith("//"):
                    return redirect(next_url)
                return redirect("/")
            error = True
        return render_template_string(_LOGIN_PAGE, error=error, next=next_url)

    @app.route("/logout")
    def logout():
        session.pop("dashboard_auth", None)
        return redirect("/login")

    @app.route("/chat")
    def standalone_chat():
        if _pin_required() and not _pin_ok():
            return redirect("/login?next=/chat")
        return send_from_directory(app.static_folder, "chat.html")

    @app.route("/wizard")
    def wizard():
        if _pin_required() and not _pin_ok():
            return redirect("/login?next=/wizard")
        return send_from_directory(app.static_folder, "wizard.html")

    @app.route("/dashboard")
    def dashboard():
        if _pin_required() and not _pin_ok():
            return redirect("/login?next=/dashboard")
        return send_from_directory(app.static_folder, "dashboard.html")

    # Legacy login/logout aliases so old bookmarks still work
    @app.route("/dashboard/login", methods=["GET", "POST"])
    def dashboard_login():
        return redirect("/login?next=/dashboard")

    @app.route("/dashboard/logout")
    def dashboard_logout():
        session.pop("dashboard_auth", None)
        return redirect("/login?next=/dashboard")

    return app


app = create_app()
