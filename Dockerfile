# Multi-stage build for WhatsApp MCP Server
FROM golang:1.24-alpine AS go-builder

# Install build dependencies for CGO (needed for SQLite)
RUN apk add --no-cache gcc musl-dev sqlite-dev

WORKDIR /app/whatsapp-bridge
COPY whatsapp-bridge/ .
RUN go mod download
RUN CGO_ENABLED=1 go build -o whatsapp-bridge main.go

# Final stage
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libsqlite3-0 \
    ffmpeg \
    supervisor \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for Python package management
RUN pip install uv

WORKDIR /app

# Copy Go binary
COPY --from=go-builder /app/whatsapp-bridge/whatsapp-bridge /app/whatsapp-bridge/whatsapp-bridge

# Copy Python MCP server
COPY whatsapp-mcp-server/ /app/whatsapp-mcp-server/

# Install Python dependencies
WORKDIR /app/whatsapp-mcp-server
RUN uv venv && uv sync

# Copy startup and config files
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Create data directory for SQLite and set permissions
RUN mkdir -p /app/whatsapp-bridge/store && chmod 777 /app/whatsapp-bridge/store

# Set environment variables
ENV MESSAGES_DB_PATH=/app/whatsapp-bridge/store/messages.db
ENV WHATSAPP_API_BASE_URL=http://localhost:8080/api
ENV MCP_PORT=3000

WORKDIR /app

# Expose ports: 8080 for Go bridge, 3000 for MCP server
EXPOSE 8080 3000

CMD ["/app/start.sh"]
