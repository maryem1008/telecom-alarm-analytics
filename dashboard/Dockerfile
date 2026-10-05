FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir \
    streamlit==1.36.0 pandas==2.2.2 altair==5.3.0 pydeck==0.9.1 \
    psycopg2-binary==2.9.9 reportlab==4.2.2 python-dotenv==1.0.1 \
    bcrypt==4.2.1

COPY common/ common/
COPY dashboard/ dashboard/
COPY report/ report/
COPY remediation/ remediation/
COPY manage_users.py .
COPY .streamlit/ .streamlit/

EXPOSE 8501

CMD ["streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0"]
