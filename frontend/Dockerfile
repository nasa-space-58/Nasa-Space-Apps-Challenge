FROM node:22.21.1-slim
WORKDIR /workspace
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
CMD ["npm", "run", "dev"]
