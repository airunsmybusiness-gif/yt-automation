FROM python:3.12-slim

# FFmpeg for video rendering
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "execution.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
