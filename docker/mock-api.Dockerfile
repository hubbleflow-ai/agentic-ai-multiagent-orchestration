# Shared base image for the 3 mock external APIs (airline, hotel, payment).
# Parameterised by API_NAME build arg so one Dockerfile builds all three.

FROM python:3.12-slim

ARG API_NAME
ENV API_NAME=${API_NAME}
# Make /app importable so `uvicorn services.mock_apis.<name>.main:app` resolves.
ENV PYTHONPATH=/app

RUN pip install --no-cache-dir fastapi 'uvicorn[standard]' pydantic

WORKDIR /app

# Copy just the mock APIs (lighter than the full agent image).
COPY services/__init__.py ./services/__init__.py
COPY services/mock_apis/__init__.py ./services/mock_apis/__init__.py
COPY services/mock_apis/${API_NAME}/ ./services/mock_apis/${API_NAME}/

# Each mock API listens on a different port (airline 9001, hotel 9002,
# payment 9003) — the compose file maps these.
EXPOSE 9001 9002 9003

# Pick the right port for this API_NAME at runtime.
CMD ["sh", "-c", "case ${API_NAME} in airline) PORT=9001;; hotel) PORT=9002;; payment) PORT=9003;; esac; exec uvicorn services.mock_apis.${API_NAME}.main:app --host 0.0.0.0 --port ${PORT}"]
