FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
FROM node:18-alpine
WORKDIR /app

# install dependencies
COPY package*.json ./
RUN npm install

# copy source
COPY . .

# expose vite dev server
EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]