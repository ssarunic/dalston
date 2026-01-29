# Stub Transcriber Engine
#
# Extends the base engine image with stub transcription logic.
# Returns hardcoded transcript for testing the pipeline.
#
# Build from repo root:
#   docker compose build engine-stub-transcriber

FROM dalston/engine-base:latest

# Copy engine files
COPY engines/transcribe/stub-transcriber/requirements.txt .
COPY engines/transcribe/stub-transcriber/engine.yaml .
COPY engines/transcribe/stub-transcriber/engine.py .

# Install any additional dependencies (none for stub)
RUN pip install --no-cache-dir -r requirements.txt || true

CMD ["python", "engine.py"]
