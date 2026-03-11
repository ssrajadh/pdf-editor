FROM node:20-slim

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ .

EXPOSE 5173

CMD ["npx", "vite", "--host", "0.0.0.0"]
