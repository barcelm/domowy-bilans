FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    APP_TZ=Europe/Warsaw

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # zbuduj cache czcionek matplotlib w obrazie -> szybszy pierwszy start
    && python -c "import matplotlib.pyplot"

COPY . .

# Cloud Run podaje port w zmiennej PORT (domyślnie 8080)
CMD ["sh", "-c", "exec shiny run app.py --host 0.0.0.0 --port ${PORT:-8080}"]
