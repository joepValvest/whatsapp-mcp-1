# Multi-stage build for WhatsApp MCP Server
FROM golang:1.24-alpine AS go-builder

# Install build dependencies for CGO (needed for SQLite)
RUN apk add --no-cache gcc musl-dev sqlite-dev

WORKDIR /build
COPY whatsapp-bridge/go.mod whatsapp-bridge/go.sum ./
RUN go mod download

COPY whatsapp-bridge/*.go ./
# Build with static linking for glibc compatibility
RUN CGO_ENABLED=1 GOOS=linux GOARCH=amd64 go build -ldflags="-w -s -linkmode external -extldflags '-static'" -o whatsapp-bridge .
RUN chmod +x whatsapp-bridge && ls -la /build/

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

# Copy Go binary - verify it exists
COPY --from=go-builder /build/whatsapp-bridge /app/whatsapp-bridge/whatsapp-bridge
RUN ls -la /app/whatsapp-bridge/ && chmod +x /app/whatsapp-bridge/whatsapp-bridge

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
ENV WHATSAPP_API_BASE_URL=http://localhost:8081/api
ENV MCP_PORT=3000
# Supabase environment variables (set via Railway dashboard)
ENV SUPABASE_URL=""
ENV SUPABASE_KEY=""

WORKDIR /app

# Expose ports: 8080 for Go bridge internal, 3000 for MCP server (Railway uses PORT)
EXPOSE 3000 8080

CMD ["/app/start.sh"]
