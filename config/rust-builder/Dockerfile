ARG BASE_IMAGE=rust:1.63.0-bullseye
FROM ${BASE_IMAGE}
ARG TARGET=x86_64-unknown-linux-gnu
# Compile on the Docker host's native CPU; only the output target is crossed.
RUN case "$TARGET" in \
      x86_64-unknown-linux-gnu) packages='gcc-x86-64-linux-gnu libc6-dev-amd64-cross' ;; \
      aarch64-unknown-linux-gnu) packages='gcc-aarch64-linux-gnu libc6-dev-arm64-cross' ;; \
      *) echo "unsupported target: $TARGET" >&2; exit 1 ;; \
    esac && apt-get update && apt-get install -y --no-install-recommends $packages \
    && rm -rf /var/lib/apt/lists/* && rustup target add "$TARGET"
ENV CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc \
    CC_x86_64_unknown_linux_gnu=x86_64-linux-gnu-gcc \
    AR_x86_64_unknown_linux_gnu=x86_64-linux-gnu-ar \
    CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc \
    CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc \
    AR_aarch64_unknown_linux_gnu=aarch64-linux-gnu-ar \
    CARGO_TARGET_DIR=/cache/target
WORKDIR /work
