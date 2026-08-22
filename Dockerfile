FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

EXPOSE 5080

# 개발용 서버(app.run) 대신 gunicorn으로 구동. worker는 1개만 두되 스레드로
# 동시 요청을 처리한다 — _run_batch_state/_login_failures 같은 상태를 프로세스
# 메모리에 들고 있어서, worker를 여러 개로 늘리면 이 상태가 worker마다 따로
# 놀아 run_batch 쿨다운/로그인 잠금이 제대로 안 걸린다. gunicorn을 쓰는 이유는
# 그 자체로도: 느린 요청 하나가 전체를 막지 않고, 워커가 죽으면 자동 재기동됨.
CMD ["gunicorn", "--bind", "0.0.0.0:5080", "--workers", "1", "--threads", "4", \
     "--worker-class", "gthread", "--timeout", "60", \
     "--access-logfile", "-", "--error-logfile", "-", "web_admin:app"]
