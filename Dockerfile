# Single image for both backend (Python 3.14) and frontend (Node 20) runs --
# the two never run concurrently (one issue at a time, see AGENT_RULES.md), so
# there's no benefit to splitting this into two images, just two Dockerfiles
# to keep in sync.
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

# Example of a tool the target repo's definition of done needs but that
# doesn't belong in its requirements (here, semgrep, which can clash with app
# dependency pins). Installed in its own venv with just the binary on PATH.
# Remove or replace with whatever your repos need.
RUN python3 -m venv /opt/semgrep \
    && /opt/semgrep/bin/pip install --no-cache-dir semgrep==1.166.0 \
    && ln -s /opt/semgrep/bin/semgrep /usr/local/bin/semgrep

# Without this, run_task.py's own print() calls sit in Python's block buffer
# (stdout isn't a tty once `docker run`'s output is redirected to a log file)
# and only flush at process exit -- a run mid-flight looks like dead silence
# in the log even though it's actively working.
ENV PYTHONUNBUFFERED=1

COPY docker/run_task.py /agent/run_task.py

ENTRYPOINT ["python3", "/agent/run_task.py"]
