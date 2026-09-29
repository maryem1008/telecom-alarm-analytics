FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir psycopg2-binary==2.9.9 python-dotenv==1.0.1

COPY common/ common/
COPY generator/ generator/

CMD ["python", "generator/generate_alarms.py"]
