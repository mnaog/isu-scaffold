ARG RUST_IMAGE
FROM ${RUST_IMAGE}
ARG EXTRA_PACKAGES=""
RUN if [ -n "$EXTRA_PACKAGES" ]; then apt-get update \
    && apt-get install -y --no-install-recommends $EXTRA_PACKAGES \
    && rm -rf /var/lib/apt/lists/*; fi
COPY start.sh /local-start.sh
