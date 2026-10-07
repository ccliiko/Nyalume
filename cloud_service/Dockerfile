FROM node:22-alpine AS assets
WORKDIR /pet
COPY nyalume/frontends/pet/pet3d/package*.json ./
RUN npm ci --omit=dev --ignore-scripts

FROM python:3.12-slim

WORKDIR /app
COPY cloud_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY cloud_service /app/cloud_service
COPY nyalume/core/daily_content.py /app/nyalume/core/daily_content.py
COPY nyalume/frontends/web/static/daily_nyalume /app/nyalume/frontends/web/static/daily_nyalume
COPY nyalume/frontends/pet/pet3d/viewer.html /app/nyalume/frontends/pet/pet3d/viewer.html
COPY nyalume/frontends/pet/pet3d/motions/idle.vmd /app/nyalume/frontends/pet/pet3d/motions/idle.vmd
COPY --from=assets /pet/node_modules/three /app/nyalume/frontends/pet/pet3d/node_modules/three

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 nyalume
USER nyalume

EXPOSE 8080
CMD ["uvicorn", "cloud_service.app:app", "--host", "0.0.0.0", "--port", "8080"]
