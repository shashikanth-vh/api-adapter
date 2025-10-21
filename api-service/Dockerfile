# Dockerfile
FROM python:3.9-slim

WORKDIR /app

# Copy only requirements first to leverage layer cache
COPY requirements.txt .

# Ensure apt cache updated for any future needs (kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and install dependencies
RUN python -m pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# Copy app last so rebuilds are faster when code changes
COPY app.py .

# Port and entry
EXPOSE 6000
CMD ["python", "app.py"]
