FROM kicad/kicad:9.0-full

# Install runtime dependencies
RUN apt-get update -y \
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
