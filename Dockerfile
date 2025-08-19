FROM kicad/kicad:9.0-full

# Ensure root for package installation (base image may set a non-root user)
USER root

# Install runtime dependencies
RUN mkdir -p /var/lib/apt/lists/partial \
    && chmod 0755 /var/lib/apt/lists/partial \
    && apt-get update -y \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        jq \
        bash \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install pcb CLI (latest)
RUN curl --proto '=https' --tlsv1.2 -LsSf https://github.com/diodeinc/pcb/releases/latest/download/pcb-installer.sh | sh
ENV PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

# Default back to root to avoid permission issues when writing artifacts
USER root
