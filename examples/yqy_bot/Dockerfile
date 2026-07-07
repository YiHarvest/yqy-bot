FROM rust:1-bookworm

ENV DEBIAN_FRONTEND=noninteractive

# 安装时区数据
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    libssl-dev \
    pkg-config \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    tzdata \
  && rm -rf /var/lib/apt/lists/*

# 设置默认时区为中国
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PATH="/root/.cargo/bin:/root/.local/bin:${PATH}"

WORKDIR /workspace/iamai
