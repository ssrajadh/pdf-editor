FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y curl nodejs npm && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    aider-chat \
    pytest \
    ruff \
    mypy

CMD ["/bin/bash"]

