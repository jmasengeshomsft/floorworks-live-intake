FROM python:3.12-slim
WORKDIR /app
# Live notes after transfer (Azure Speech). Omit with: --build-arg WITH_SPEECH=0
ARG WITH_SPEECH=1
COPY requirements.txt requirements-speech.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$WITH_SPEECH" = "1" ]; then \
         apt-get update && apt-get install -y --no-install-recommends libasound2 ca-certificates \
         && rm -rf /var/lib/apt/lists/* \
         && pip install --no-cache-dir -r requirements-speech.txt; \
       fi
COPY app ./app
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --ws-max-size 1048576"]
