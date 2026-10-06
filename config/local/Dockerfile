ARG RUST_IMAGE
FROM ${RUST_IMAGE}
# Keep the inspected OS/glibc base; select the compiler independently.
RUN rustup toolchain install 1.99.0 --profile minimal && rustup default 1.99.0
ENV RUSTUP_TOOLCHAIN=1.99.0
ARG EXTRA_PACKAGES=""
RUN if [ -n "$EXTRA_PACKAGES" ]; then apt-get update \
    && apt-get install -y --no-install-recommends $EXTRA_PACKAGES \
    && rm -rf /var/lib/apt/lists/*; fi
COPY start.sh /local-start.sh
