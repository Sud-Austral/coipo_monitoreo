# Imagen del modo contenedor, para el ambiente de test (vm3).
#
# El modo recomendado para produccion sigue siendo systemd o cron sobre el host: un
# contenedor le agrega al monitoreo una dependencia del demonio Docker, que es una de
# las cosas que tumban aplicaciones. Ver README.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Santiago

# tzdata para que las horas locales del reporte sean las de Chile y no UTC.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt

COPY coipo_monitoreo/ ./coipo_monitoreo/
COPY config/ ./config/

# El historial vive en un volumen, no en la carpeta de la aplicacion. Es deliberado:
# el rsync del pipeline corre con --delete y solo preserva .env y data/, asi que una
# base guardada dentro de /opt/apps/coipo_monitoreo/datos/ se borraria en cada
# despliegue y el historial no serviria para nada.
RUN useradd --system --uid 10001 --create-home monitoreo \
 && mkdir -p /datos \
 && chown -R monitoreo:monitoreo /datos /app
USER monitoreo

ENV COIPO_CONFIG=/app/config/dominios.yml \
    COIPO_BASE_DATOS=/datos/monitoreo.db \
    COIPO_ARCHIVO_LOG=/datos/coipo_monitoreo.log \
    COIPO_INTERVALO_MIN=15

EXPOSE 8000

# Sin curl en la imagen: el healthcheck usa el interprete que ya esta adentro.
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status==200 else 1)"

CMD ["uvicorn", "coipo_monitoreo.web:app", "--host", "0.0.0.0", "--port", "8000"]
