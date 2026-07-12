FROM python:3.11-slim

WORKDIR /app

# Copy only requirements for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Create data dir and unprivileged user
RUN mkdir -p /app/data /app/logs && \
    useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app /tmp

USER appuser

# Run the screener
CMD ["python", "-u", "run.py"]
