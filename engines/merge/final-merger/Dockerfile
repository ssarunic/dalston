# Final Merger Engine
#
# Combines pipeline outputs into standard transcript format.
# No GPU required - pure data transformation.
#
# Build from repo root:
#   docker compose build engine-final-merger

FROM dalston/engine-base:latest

# Copy engine files
COPY engines/merge/final-merger/requirements.txt .
COPY engines/merge/final-merger/engine.yaml .
COPY engines/merge/final-merger/engine.py .

# Install any additional Python dependencies
RUN pip install --no-cache-dir -r requirements.txt || true

CMD ["python", "engine.py"]
