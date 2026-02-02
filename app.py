from flask import Flask, send_from_directory
from flask_cors import CORS

# Blueprints (your split routes)
from routes.health import health_bp
from routes.bustime import bustime_bp
from routes.agent_api import bp as agent_bp
from routes.schedule_api import schedule_bp

# If you have web index routes, keep this import.
# If the file doesn't exist or you don't want it right now, you can delete these 2 lines.
try:
    from routes.web_index_api import web_index_bp
except Exception:
    web_index_bp = None


def create_app() -> Flask:
    app = Flask(__name__, static_folder="public_html", static_url_path="/static")
    CORS(app)

    # Register routes
    app.register_blueprint(health_bp)     # /api/health
    app.register_blueprint(bustime_bp)    # /api/routes, /api/predictions, etc.
    app.register_blueprint(agent_bp)      # /api/agent
    app.register_blueprint(schedule_bp)   # /api/schedule/debug (optional)

    if web_index_bp:
        app.register_blueprint(web_index_bp)

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/chat")
    def standalone_chat():
        return send_from_directory(app.static_folder, "chat.html")

    @app.route("/wizard")
    def wizard():
        return send_from_directory(app.static_folder, "wizard.html")

    return app


app = create_app()
