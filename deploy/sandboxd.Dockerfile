# sandboxd: the only component that talks to Docker. Stdlib Python + the docker CLI, nothing else.
#
#   docker build -f deploy/sandboxd.Dockerfile -t sentinel-sandboxd sandboxd/
FROM python:3.12-alpine
RUN apk add --no-cache docker-cli
WORKDIR /srv
COPY server.py .
ENV SANDBOXD_HOST=0.0.0.0 SANDBOXD_PORT=8090 PYTHONUNBUFFERED=1
EXPOSE 8090
CMD ["python", "server.py"]
