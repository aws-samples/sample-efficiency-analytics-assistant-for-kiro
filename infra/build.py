"""Build Lambda assets locally (no Docker).

- build_api_assets():
    1) install the dashboard API dependencies (fastapi/mangum/pydantic/...) as manylinux2014 x86_64 / cp312 wheels
       into build/api_deps/python (Lambda layer layout);
    2) flatten backend/api/* + backend/shared/* (including derivations, athena_client) into build/api_app,
       so handler.py's `from main import app` and `from shared...` relative fallbacks work.
  Returns (deps_layer_dir, app_code_dir).

The design matches data_stack's ETL packaging: no Docker bundling.
Dependency installation uses a cache stamp (reinstall only when requirements change).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
BACKEND = os.path.abspath(os.path.join(HERE, "..", "backend"))

API_DEPS = [
    "fastapi==0.143.2",
    "starlette==1.8.0",
    "mangum==0.17.0",
    "pydantic==2.9.2",
    "pydantic-core==2.23.4",
    "anyio==4.15.1",
    "idna==3.20",
    "typing_extensions==4.16.0",
    "annotated-types==0.8.0",
    "annotated-doc==0.0.5",
    "typing-inspection==0.4.4",
    "opentelemetry-api==1.45.1",
]


def _sig(items: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(items)).encode()).hexdigest()[:16]


def build_api_assets() -> tuple[str, str]:
    deps_dir = os.path.join(BUILD, "api_deps")
    app_dir = os.path.join(BUILD, "api_app")
    _build_deps_layer(deps_dir)
    _assemble_app(app_dir)
    return deps_dir, app_dir


def _build_deps_layer(deps_dir: str) -> None:
    py_dir = os.path.join(deps_dir, "python")
    stamp = os.path.join(deps_dir, ".sig")
    sig = _sig(API_DEPS)
    if os.path.isfile(stamp) and open(stamp, encoding="utf-8").read().strip() == sig and os.path.isdir(py_dir):
        return  # already built and dependencies unchanged
    if os.path.isdir(deps_dir):
        shutil.rmtree(deps_dir)
    os.makedirs(py_dir, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--only-binary=:all:", "--platform", "manylinux2014_x86_64",
        "--python-version", "3.12", "--implementation", "cp",
        "--target", py_dir, *API_DEPS,
    ]
    subprocess.check_call(cmd)  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- static argv from in-repo constants (sys.executable, fixed pip flags, API_DEPS); build-time only, no external input
    _trim(py_dir)
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(sig)


def _assemble_app(app_dir: str) -> None:
    if os.path.isdir(app_dir):
        shutil.rmtree(app_dir)
    os.makedirs(app_dir, exist_ok=True)
    # Flatten the API code (drop the package prefix; handler.py uses the `from main import app` fallback)
    for fn in ["handler.py", "main.py", "deps.py", "settings.py", "model_multipliers.py"]:
        shutil.copy2(os.path.join(BACKEND, "api", fn), app_dir)
    # Keep shared as a subpackage (athena_client uses the `from shared.athena_client` fallback)
    shared_dst = os.path.join(app_dir, "shared")
    os.makedirs(shared_dst, exist_ok=True)
    open(os.path.join(shared_dst, "__init__.py"), "w", encoding="utf-8").close()
    for fn in ["athena_client.py", "derivations.py", "recommendations.py"]:
        shutil.copy2(os.path.join(BACKEND, "shared", fn), shared_dst)


def _trim(py_dir: str) -> None:
    """Prune irrelevant files to shrink the layer size."""
    for root, dirs, files in os.walk(py_dir):
        for d in list(dirs):
            if d in ("__pycache__", "tests", "test"):
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                dirs.remove(d)
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                os.remove(os.path.join(root, f))


if __name__ == "__main__":
    d, a = build_api_assets()
    print("deps layer:", d)
    print("app code:", a)
