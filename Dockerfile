FROM python:3.11-slim

WORKDIR /app

COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

COPY web_app.py .
COPY templates/ ./templates/

EXPOSE 5000

CMD ["python", "web_app.py"]
