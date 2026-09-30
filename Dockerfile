ARG PYTHON_IMAGE=python:3.14.4-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLCONFIGDIR=/app/tmp/matplotlib \
    HF_HOME=/app/tmp/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PUBLIC_DEMO=1

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser
COPY requirements-frozen.txt /app/requirements-frozen.txt
# CPU-only torch first: the default Linux wheel pulls ~6 GB of CUDA libraries that fail
# `pip check` (nvidia-cusparselt-cu13 is unsupported on some platforms) and are unused here.
RUN python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.14.0 \
    && python -m pip install --no-cache-dir -r requirements-frozen.txt \
    && python -m pip check \
    && python -m pip freeze > /opt/image-requirements.txt

COPY --chown=appuser:appuser . /app
RUN mkdir -p /app/tmp /app/output /app/dataset/retrieval/embeddings \
    && chown -R appuser:appuser /app
USER appuser

# Explicit public model download. No API credentials or generation calls are used.
# Set to 0 for a smaller image supporting BM25, EDA and saved-SQL verification.
ARG INCLUDE_E5=1
RUN if [ "$INCLUDE_E5" = "1" ]; then \
      python -m src.model_training --prepare-encoder --download-model; \
    else python -m src.model_training; fi

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"
CMD ["python", "-m", "streamlit", "run", "app/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
