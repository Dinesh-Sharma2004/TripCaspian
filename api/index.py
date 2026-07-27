"""Vercel Serverless Function entrypoint for TripCaspian agent."""

import os
import json
import logging
from http.server import BaseHTTPRequestHandler
from caspian_sdk import CommClient
from tripcaspian.service import TripService
from tripcaspian.storage import SQLiteStorage

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tripcaspian_vercel")

# Initialize persistent instances at module scope for serverless reuse
client = CommClient()
storage = SQLiteStorage(db_path="/tmp/tripcaspian.db")
service = TripService(storage=storage, caspian_client=client)


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
            event = json.loads(post_data) if post_data else {}
            event_type = event.get("type")
            seq = event.get("seq")

            logger.info("Webhook received: event_type=%s, seq=%s", event_type, seq)

            if event_type == "message.received":
                data = event.get("data", {})
                message_data = data.get("message", {})

                msg_id = message_data.get("id")
                conv_id = message_data.get("conversation_id", "vercel_default_conv")
                text = message_data.get("text", "")
                sender = message_data.get("sender")

                logger.info(
                    "Processing message: msg_id=%s, conv_id=%s, sender=%s",
                    msg_id,
                    conv_id,
                    sender,
                )

                if text:
                    reply = service.handle_user_message(conv_id, sender, text)

                    if msg_id:
                        try:
                            client.reply(msg_id, text=reply)
                            logger.info("Reply sent via Caspian REST API for msg_id=%s", msg_id)
                        except Exception as reply_err:
                            logger.exception("Reply failure for msg_id=%s: %s", msg_id, reply_err)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "seq": seq}).encode("utf-8"))

        except Exception as e:
            logger.exception("Webhook handler processing exception: %s", e)
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Internal server error"}).encode("utf-8"))
