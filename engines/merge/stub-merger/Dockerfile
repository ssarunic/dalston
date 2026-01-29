# Stub Merger Engine
#
# Extends the base engine image with stub merger logic.
# Passes through transcript and writes canonical output for testing the pipeline.
#
# Build from repo root:
#   docker compose build engine-stub-merger

FROM dalston/engine-base:latest

# Copy engine files
COPY engines/merge/stub-merger/requirements.txt .
COPY engines/merge/stub-merger/engine.yaml .
COPY engines/merge/stub-merger/engine.py .

# Install any additional dependencies (none for stub)
RUN pip install --no-cache-dir -r requirements.txt || true

CMD ["python", "engine.py"]
