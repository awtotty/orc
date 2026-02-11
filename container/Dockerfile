FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    git \
    tmux \
    curl \
    build-essential \
    openssh-client \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv \
    ripgrep \
    neovim \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /usr/lib/python*/EXTERNALLY-MANAGED

# Install pnpm and lazygit
RUN npm install -g pnpm \
    && LAZYGIT_VERSION=$(curl -s https://api.github.com/repos/jesseduffield/lazygit/releases/latest | grep '"tag_name"' | sed 's/.*"v\(.*\)".*/\1/') \
    && curl -Lo /tmp/lazygit.tar.gz "https://github.com/jesseduffield/lazygit/releases/download/v${LAZYGIT_VERSION}/lazygit_${LAZYGIT_VERSION}_Linux_x86_64.tar.gz" \
    && tar xf /tmp/lazygit.tar.gz -C /usr/local/bin lazygit \
    && rm /tmp/lazygit.tar.gz

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/ \
    && mv /root/.local/bin/uvx /usr/local/bin/

# Install Claude Code (native binary)
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && cp -L /root/.local/bin/claude /usr/local/bin/claude \
    && rm -rf /root/.local/share/claude

# Create agent user matching host UID/GID for file permissions
ARG UID=1000
ARG GID=1000
RUN userdel -r ubuntu 2>/dev/null; groupdel ubuntu 2>/dev/null; \
    groupadd -g ${GID} agent \
    && useradd -m -s /bin/bash -u ${UID} -g ${GID} agent

# Ensure ~/.local/bin is in PATH for all shells (bash, tmux panes, etc.)
RUN echo 'export PATH="$HOME/.local/bin:$PATH"' >> /etc/bash.bashrc

USER agent

CMD ["sleep", "infinity"]
