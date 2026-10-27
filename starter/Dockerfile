FROM python:3.12-slim

# Optional proxy for corporate networks: docker compose build --build-arg HTTP_PROXY=http://...
ARG HTTP_PROXY
ARG HTTPS_PROXY
ENV http_proxy=$HTTP_PROXY https_proxy=$HTTPS_PROXY

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY starter/requirements.lock.txt /app/requirements.lock.txt
RUN pip install --no-cache-dir -r requirements.lock.txt

# Clear proxy env so it doesn't leak into runtime
ENV http_proxy= https_proxy=

COPY starter/ /app/
COPY fixtures/ /fixtures/
COPY contracts/ /app/contracts/
COPY prompts/ /prompts/
COPY rules/ /rules/

RUN mkdir -p /data /app/staticfiles \
    && useradd --uid 10001 --create-home app \
    && chown -R app:app /data /app

USER app
RUN python manage.py collectstatic --noinput

ENV DATA_DIR=/data FIXTURE_DIR=/fixtures RULES_DIR=/rules PROMPTS_DIR=/prompts

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "1", "--threads", "2", "--timeout", "90"]
