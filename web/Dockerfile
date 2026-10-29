FROM node:22-alpine
RUN corepack enable
WORKDIR /srv
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
RUN pnpm build
EXPOSE 5173
CMD ["pnpm", "preview"]
