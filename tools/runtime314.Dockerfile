FROM archlinux:base@sha256:e82623936fa1a69700b48e98b058df6eaab18b737c27473a7234a40fdecb9413
# Pacman's downloader seccomp sandbox is unsupported by QEMU/binfmt containers.
# Package signature verification remains enabled.
RUN printf 'Server = https://archive.archlinux.org/repos/2026/10/30/$repo/os/$arch\n' > /etc/pacman.d/mirrorlist \
    && pacman -Syu --disable-sandbox --noconfirm --needed python python-pip \
    deluge python-setuptools python-wheel make git chromium \
    && pacman -Scc --noconfirm
WORKDIR /work
COPY requirements-dev314.txt constraints314.txt /opt/requirements/
RUN python -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install -r /opt/requirements/requirements-dev314.txt
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1
CMD ["make", "check", "PYTHON=python", "VENV=/opt/venv"]
