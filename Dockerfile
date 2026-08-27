FROM python:3.12-slim

# tzdata ist Pflicht, nicht Komfort: der Abtastplan hängt an 12:00 Wiener Zeit.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*
ENV TZ=Europe/Vienna PYTHONUNBUFFERED=1

# Keine Abhängigkeiten — reine Standardbibliothek, daher kein requirements.txt.
WORKDIR /app
COPY tankradar.py ./
COPY web/ ./web/

# Unprivilegiert laufen; /data ist das einzige beschreibbare Verzeichnis.
RUN useradd -r -u 10001 -m tankradar && mkdir -p /data && chown tankradar:tankradar /data
USER tankradar
VOLUME ["/data"]
ENV TANKRADAR_DATA=/data
EXPOSE 842

HEALTHCHECK --interval=60s --timeout=5s --start-period=300s --retries=3 \
  CMD python3 -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:842/healthz',timeout=4).status==200 else 1)"

ENTRYPOINT ["python3", "tankradar.py"]
CMD ["run", "--host", "0.0.0.0", "--port", "842"]
