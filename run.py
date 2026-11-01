"""Chạy máy chủ phát triển: python run.py"""
import os

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402
from app.extensions import socketio  # noqa: E402

app = create_app()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
                 debug=os.environ.get("FLASK_DEBUG") == "1", allow_unsafe_werkzeug=True)
