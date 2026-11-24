FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Timezone configuration (Europe/Berlin)
ENV TZ=Europe/Berlin
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies (tzdata for exact German timezone, curl for healthcheck, libjpeg/zlib for Pillow)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    ca-certificates \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Default environment variables
ENV HOST=0.0.0.0
ENV PORT=8000
ENV ENV=production

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["python", "run.py"]
