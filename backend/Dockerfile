FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 의존성 레이어를 코드와 분리한다. 코드 한 줄 고칠 때마다 의존성을 다시 받으면
# CI 한 번에 수 분이 날아간다. 빈 패키지로 먼저 설치해 의존성만 굳힌 뒤,
# 실제 코드는 아래에서 덮어쓴다.
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
 && pip install --no-cache-dir -e .
# extras를 설치하지 않는 것이 이 이미지의 핵심이다.
#
# 공모전 배포는 취향 근거 RAG(`[embeddings]`)와 사진 분위기(`[mood]`)를 끄고 뜬다
# (`TASTE_EVIDENCE_ENABLED=false`, `PLACE_MOOD_ENABLED=false`). 두 extras는 torch를
# 끌고 오고 모델 가중치까지 구워야 해서 이미지가 2.3GB, 상주 메모리가 1.5GB가 된다 —
# 켜지도 않을 기능 때문에 t3.micro(1GB)에 들어가지 못하는 이미지가 만들어진다.
#
# 코드를 지우지 않아도 되는 이유는 두 인코더의 import가 전부 함수 안에 있고
# (`app/providers/place_evidence_encoder.py`, `place_mood_encoder.py`),
# 플래그가 꺼져 있으면 팩토리가 인코더를 아예 만들지 않기 때문이다
# (`app/providers/factory.py`). 즉 패키지가 없어도 부팅과 요청 처리가 멀쩡하다.
#
# 기능을 켜려면 여기를 `pip install --no-cache-dir -e ".[embeddings,mood]"`로 바꾸고
# 본 저장소 Dockerfile처럼 모델을 굽는 단계를 되살려야 한다. 플래그만 켜면
# 첫 요청에서 ImportError가 난다.

COPY app/ ./app/
COPY resources/ ./resources/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
