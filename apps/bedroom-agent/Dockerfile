FROM python:3.10-slim

# Good defaults for containers
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# (Optional) system deps for packages that need compiling.
# Keep minimal; add more only if pip install fails.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better caching)
COPY pyproject.toml ./
# If you have a lock file, copy it too:
# COPY uv.lock poetry.lock pdm.lock requirements*.txt ./

RUN pip install --upgrade pip \
    && pip install --no-cache-dir "uvicorn[standard]"

# Install your project (reads deps from pyproject.toml)
COPY . .
RUN pip install --no-cache-dir .

EXPOSE 9000

CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "9000"]