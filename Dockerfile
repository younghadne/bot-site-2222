FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p sessions

EXPOSE 10000

CMD ["python", "-m", "gunicorn", "-w", "1", "--threads", "100", "--bind", "0.0.0.0:10000", "--timeout", "120", "web_app:app"]
