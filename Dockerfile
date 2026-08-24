FROM python:3.13-slim

# Install git and SSH client for cloning repos
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# SSH config: don't prompt for host key verification and add known hosts
RUN mkdir -p /root/.ssh && \
    chmod 700 /root/.ssh && \
    printf "Host *\n  StrictHostKeyChecking no\n  UserKnownHostsFile /dev/null\n  LogLevel ERROR\n" > /root/.ssh/config && \
    chmod 600 /root/.ssh/config

WORKDIR /app

# Copy source code
COPY src/ /app/src/
COPY ui/ /app/ui/
COPY service_catalog/ /app/service_catalog/
COPY pyproject.toml /app/

# Install the Python package and UI API dependencies
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir \
      flask \
      flask-cors \
      pymongo \
      pyyaml \
      gunicorn

# Git credentials for the repo-cloning feature (team scan / repo_cloner.py).
# Provide these at `docker run` time via -e, or mount a secret. Never bake
# real credentials into the image.
# ENV DEFAULT_GIT_USERNAME=""
# ENV DEFAULT_GIT_API_TOKEN=""

# Expose port
EXPOSE 1001

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:1001/api/health')" || exit 1

# Run with gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:1001", "--workers", "2", "--timeout", "300", "--chdir", "/app/ui/api", "server:app"]
