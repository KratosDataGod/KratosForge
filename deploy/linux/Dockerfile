# KratosForge agent as a container. Build from the repository root:
#   docker build -f deploy/linux/Dockerfile -t fabric-desktop-agent .
# Run with the config, certificate and project mounted, and a volume for the queue:
#   docker run -d --name fd-agent \
#     -v /etc/fabric-desktop:/etc/fabric-desktop:ro \
#     -v /srv/fabric/sales-metadata:/srv/fabric/sales-metadata:ro \
#     -v fd-agent-home:/var/lib/fabric-desktop \
#     fabric-desktop-agent
# Pinned by digest, so a rebuild uses the same base image until a PR moves it. To update, read the
# current digest (docker buildx imagetools inspect python:3.12-slim) and replace it here.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

RUN useradd --system --create-home --home-dir /var/lib/fabric-desktop fabric-desktop
COPY sidecar /tmp/sidecar
RUN pip install --no-cache-dir -c /tmp/sidecar/constraints.txt /tmp/sidecar && rm -rf /tmp/sidecar

ENV FABRIC_DESKTOP_AGENT_CONFIG=/etc/fabric-desktop/agent.toml \
    FABRIC_DESKTOP_HOME=/var/lib/fabric-desktop \
    PYTHONUNBUFFERED=1
USER fabric-desktop
VOLUME ["/var/lib/fabric-desktop"]
# docker stop sends SIGTERM: the agent finishes running jobs first (use --stop-timeout for long ones).
STOPSIGNAL SIGTERM
ENTRYPOINT ["fabric-desktop", "agent"]
CMD ["run"]
