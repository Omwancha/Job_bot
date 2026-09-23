FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# ── System deps: Google Chrome (for Selenium), tzdata, fonts ───────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates tzdata fonts-liberation \
    && wget -q -O /tmp/chrome.deb \
        https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm -f /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/*

# ── supercronic: a cron replacement built for containers ───────────────────────
# (runs the schedule OUTSIDE the Python process, logs to stdout, passes env through)
ENV SUPERCRONIC_VERSION=v0.2.33
RUN wget -q \
        "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
        -O /usr/local/bin/supercronic \
    && chmod +x /usr/local/bin/supercronic

WORKDIR /app

# Install Python deps first for better layer caching
COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-cache the Selenium (chromedriver) binary so the first run needn't download it
RUN python -c "from selenium import webdriver; from selenium.webdriver.chrome.options import Options; o=Options(); [o.add_argument(a) for a in ('--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-gpu')]; d=webdriver.Chrome(options=o); d.quit(); print('selenium driver cached')" \
    || echo "selenium pre-warm skipped (driver will resolve at runtime)"

# App source (secrets and state are excluded via .dockerignore)
COPY . .

# Normalise line endings (repo authored on Windows) and make the entrypoint runnable
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh && chmod +x /app/docker-entrypoint.sh

# Persistent state (SQLite dedup DB + optional Gmail OAuth token) lives on a volume
RUN mkdir -p /data/credentials
ENV DB_PATH=/data/jobbot.db

ENTRYPOINT ["/app/docker-entrypoint.sh"]
