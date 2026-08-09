FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Set PYTHONPATH so that python can import modules inside src/
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Startup command
CMD ["python", "-m", "bizpulse.agent"]
