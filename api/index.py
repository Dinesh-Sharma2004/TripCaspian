"""Vercel Serverless Function entrypoint for TripCaspian agent."""

import os
import json
from http.server import BaseHTTPRequestHandler
from tripcaspian.service import TripService
from tripcaspian.storage import SQLiteStorage

storage = SQLiteStorage(db_path="/tmp/tripcaspian.db")
service = TripService(storage=storage)


class handler(BaseHTTPRequestHandler):
    """Vercel Python Serverless HTTP Request Handler."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()

        response = {
            "status": "online",
            "service": "TripCaspian AI Agent",
            "version": "0.1.0",
            "telegram_bot": "@tripcaspian_bot",
            "channels": ["telegram", "discord", "email"],
            "providers": ["IRCTC Train", "redBus Bus", "Uber Cab"],
        }
        self.wfile.write(json.dumps(response, indent=2).encode("utf-8"))

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8")

        try:
            payload = json.loads(post_data) if post_data else {}
            conv_id = payload.get("conversation_id", "vercel_default_conv")
            text = payload.get("text", "")
            sender = payload.get("sender")

            reply = service.handle_user_message(conv_id, sender, text)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"reply": reply}).encode("utf-8"))
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
