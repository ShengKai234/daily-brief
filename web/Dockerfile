FROM public.ecr.aws/docker/library/node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html vite.config.ts tsconfig.json ./
COPY public ./public
COPY src ./src

RUN chown -R node:node /app
USER node

EXPOSE 5173

CMD ["npm", "run", "dev"]
