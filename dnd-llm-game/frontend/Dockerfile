# Stage 1: Build React/Vite app using Bun
FROM oven/bun:1-alpine AS builder

WORKDIR /app

# Copy dependency configs
COPY package.json bun.lock ./

# Install packages
RUN bun install --frozen-lockfile

# Copy frontend source files
COPY . .

# Set up build arguments for API address
ARG VITE_API_BASE=http://localhost:8765/api
ENV VITE_API_BASE=$VITE_API_BASE

# Build production React bundle
RUN bun run build

# Stage 2: Serve React assets using Nginx
FROM nginx:alpine

# Copy custom Nginx configuration to handle React Router client-side routing fallback
COPY <<'NGINX_CONF' /etc/nginx/conf.d/default.conf
server {
    listen 80;
    server_name localhost;

    location / {
        root /usr/share/nginx/html;
        index index.html index.htm;
        try_files $uri $uri/ /index.html;
    }

    error_page 500 502 503 504 /50x.html;
    location = /50x.html {
        root /usr/share/nginx/html;
    }
}
NGINX_CONF

# Copy built files from stage 1
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
