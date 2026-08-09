"""BizPulse Configuration Module."""

import os

# Confidence threshold for LLM commitment extraction (from engineering review: 0.65)
CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.65"))

# System timezone defaults to Asia/Kolkata (overrideable via BIZPULSE_TIMEZONE env var)
DEFAULT_TIMEZONE = os.environ.get("BIZPULSE_TIMEZONE", "Asia/Kolkata")

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH", "tripcaspian.db")
