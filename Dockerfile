FROM python:3.13-slim

ARG DEBIAN_FRONTEND=noninteractive

WORKDIR /app

COPY requirements.txt ./

RUN pip install --no-cache-dir -r requirements.txt

COPY llmcord.py bee.py satellite.py imagegen.py viewer.py voice.py viewer.html config.yaml ./
COPY sounds/ ./sounds/

CMD ["python", "llmcord.py"]
