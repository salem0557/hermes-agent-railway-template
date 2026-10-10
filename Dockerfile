FROM nousresearch/hermes-agent:v0.21.6@sha256:55e192fba0cd4fde61142abbff5adeacff40efdb482ccd0ff877924bd274f909

COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/hermes-railway-entrypoint
COPY --chmod=0755 scripts/hermes_storage.py /usr/local/bin/hermes-storage
COPY storage-skill/SKILL.md /usr/local/share/hermes-railway/skills/railway-storage/SKILL.md

RUN python -c "import boto3"

ENV HERMES_HOME=/data/.hermes \
    HERMES_WRITE_SAFE_ROOT=/data/.hermes \
    HERMES_LAZY_INSTALL_TARGET=/data/.hermes/lazy-packages \
    HERMES_STORAGE_CACHE_DIR=/data/.hermes/storage-cache \
    HERMES_STORAGE_CACHE_MAX_MB=1024 \
    HERMES_DASHBOARD=1 \
    HERMES_DASHBOARD_HOST=0.0.0.0 \
    HERMES_GATEWAY_BOOTSTRAP_STATE=running

ENTRYPOINT ["/usr/local/bin/hermes-railway-entrypoint"]
CMD ["gateway", "run"]
