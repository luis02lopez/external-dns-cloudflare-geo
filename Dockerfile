FROM python:3.12-slim

WORKDIR /app

RUN pip install kubernetes requests

COPY main.py .

CMD ["python", "main.py"]
