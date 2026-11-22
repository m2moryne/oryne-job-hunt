FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY setup_testnet.py .
ENV PYTHONUNBUFFERED=1
EXPOSE 8800
# Pass the .env values as environment variables (docker run --env-file .env ...).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8800", "--proxy-headers", "--forwarded-allow-ips", "*"]
