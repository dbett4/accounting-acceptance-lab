FROM python:3.12-slim
WORKDIR /app
COPY src/ /app/src/
RUN chmod -R a+rX /app/src && useradd --create-home --uid 10001 lab && mkdir -p /state && chown lab:lab /state
USER lab
ENV AAL_STATE_DIR=/state
ENV PYTHONPATH=/app/src
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=2s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=1)"
CMD ["python", "-m", "aal.cli", "serve", "--host", "0.0.0.0", "--port", "8080"]
