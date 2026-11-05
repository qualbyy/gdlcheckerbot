FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Данные хранятся в /data — монтируй volume туда
ENV BOT_DB_PATH=/data/bot.db

CMD ["python", "main.py"]
