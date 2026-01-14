from flask import Flask
from flask_cors import CORS

from routes.health import health_bp
from routes.bustime import bustime_bp
from routes.schedule_api import schedule_bp
from routes.web_index_api import web_index_bp
from routes.agent_api import agent_bp

app = Flask(__name__)
CORS(app)

# Register route groups (blueprints)
app.register_blueprint(health_bp)
app.register_blueprint(bustime_bp)
app.register_blueprint(schedule_bp)
app.register_blueprint(web_index_bp)
app.register_blueprint(agent_bp)

# Optional: Render/Gunicorn uses "app" automatically.
# If you run locally: python app.py
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
