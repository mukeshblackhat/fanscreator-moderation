FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /srv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 HF_HOME=/opt/hf
COPY pyproject.toml uv.lock ./
# Service deps from the lockfile. torch comes from PyTorch's CPU-only index instead of
# PyPI: PyPI's Linux wheels (x86 and arm64) pull several GB of CUDA packages that a CPU
# container never uses. Versions match the "model" extra in uv.lock.
RUN uv sync --frozen --no-dev
RUN uv pip install --python /opt/venv/bin/python --index-url https://download.pytorch.org/whl/cpu "torch==2.15.0" torchvision \
 && uv pip install --python /opt/venv/bin/python "transformers==5.22.0"
ARG REAL_MODEL=Falconsai/nsfw_image_detection
ENV REAL_MODEL=$REAL_MODEL
# Bake the model into the image so the service starts offline and fast.
RUN python -c "import os; from transformers import pipeline; pipeline('image-classification', model=os.environ['REAL_MODEL'])"
ENV HF_HUB_OFFLINE=1
COPY . .
EXPOSE 8002
CMD ["uvicorn", "realprovider.main:app", "--host", "0.0.0.0", "--port", "8002"]
