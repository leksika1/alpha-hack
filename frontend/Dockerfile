# Сборка статики фронтенда и раздача через nginx; /api проксируется на бэкенд (BACKEND_UPSTREAM) (SSE без буферизации).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime
# Шаблон: при старте nginx подставляет переменные окружения и пишет /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV BACKEND_UPSTREAM=http://api:8000
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
