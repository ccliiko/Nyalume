FROM python:3.12-slim

WORKDIR /app
COPY cloud_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY cloud_service /app/cloud_service

EXPOSE 8080
CMD ["uvicorn", "cloud_service.app:app", "--host", "0.0.0.0", "--port", "8080"]

