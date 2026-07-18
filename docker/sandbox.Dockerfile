FROM python:3.12-slim

# Everything the generated analysis code may import is baked in —
# the container runs with network_mode: none, so no runtime installs.
RUN pip install --no-cache-dir pandas==2.2.* matplotlib==3.9.* numpy==2.* pyarrow==17.*

RUN useradd -m runner
USER runner
WORKDIR /sandbox
