FROM python:3.12-slim
ENV TZ=Asia/Kolkata PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
VOLUME ["/app/reports", "/app/logs", "/app/data"]
CMD ["python", "scheduler.py"]
