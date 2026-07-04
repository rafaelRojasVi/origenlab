#!/usr/bin/env python3
"""Verify PyTorch and CUDA in the active venv (project-local only)."""
from __future__ import annotations

import sys


def main() -> None:
    print("=== check_torch_cuda.py ===")
    print("python_executable:", sys.executable)
    try:
        import torch
    except ImportError as e:
        print("torch_import:", False)
        print("error:", repr(e))
        print("Install: uv sync --group ml  # torch 2.12.1+cu129 via pyproject pytorch-cu129 index")
        sys.exit(1)

    print("torch_version:", torch.__version__)
    print("torch_cuda_is_available:", torch.cuda.is_available())
    print("torch_cuda_device_count:", torch.cuda.device_count())
    if torch.cuda.is_available():
        print("torch_cuda_device_0_name:", torch.cuda.get_device_name(0))
        print("torch_bundled_cuda:", torch.version.cuda)
    else:
        print("torch_cuda_device_0_name:", "(n/a)")
        print("torch_bundled_cuda:", torch.version.cuda)


if __name__ == "__main__":
    main()
