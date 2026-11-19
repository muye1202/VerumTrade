# VerumTrade web UI: build the Vite app, then serve it with nginx.
# nginx also proxies /api (including WebSockets) to the backend service,
# so the browser only ever talks to one origin.
FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# "same-origin" makes the app call the API on whatever host serves the page.
ARG VITE_API_BASE_URL=same-origin
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
