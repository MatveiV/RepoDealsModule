# REPO Module MVP — Makefile (Windows CMD compatible)
# Usage:
#   make demo     — generate data and run full demo on SQLite
#   make serve    — start REST API server (demo mode)
#   make test     — run all tests
#   make generate — generate demo data only
#   make clean    — remove generated files

.PHONY: demo serve test generate clean install \
        test-unit test-integration test-e2e test-cov

PYTHON = python
PIP    = pip

# Force CMD shell on Windows
SHELL       = cmd.exe
.SHELLFLAGS = /C

# Pass MODE via python -c wrapper to avoid CMD env-var scoping issues
# "set X=Y && cmd" in CMD does NOT export X to child processes reliably.
# Instead we use: python -c "import os,subprocess; os.environ['X']='Y'; subprocess.run([...])"

# ─── Install dependencies ────────────────────────────────────
install:
	$(PIP) install -r requirements.txt

# ─── Generate demo data ──────────────────────────────────────
generate:
	$(PYTHON) scripts/generate_demo_data.py

# ─── Full demo run (SQLite, no external dependencies) ────────
demo: generate
	$(PYTHON) -c "import os,subprocess,sys; os.environ['MODE']='demo'; sys.exit(subprocess.run([sys.executable,'scripts/run_demo.py']).returncode)"

# ─── Start REST API server ───────────────────────────────────
serve:
	$(PYTHON) -c "import os,subprocess,sys; os.environ['MODE']='demo'; sys.exit(subprocess.run([sys.executable,'-m','uvicorn','repo_module.api.app:app','--host','0.0.0.0','--port','8000','--reload']).returncode)"

# ─── Run tests ───────────────────────────────────────────────
test:
	$(PYTHON) -c "import os,subprocess,sys; os.environ.update({'MODE':'demo','SQLITE_PATH':':memory:'}); sys.exit(subprocess.run([sys.executable,'-m','pytest','tests/','-v','--tb=short']).returncode)"

test-unit:
	$(PYTHON) -c "import os,subprocess,sys; os.environ.update({'MODE':'demo','SQLITE_PATH':':memory:'}); sys.exit(subprocess.run([sys.executable,'-m','pytest','tests/unit/','-v']).returncode)"

test-integration:
	$(PYTHON) -c "import os,subprocess,sys; os.environ.update({'MODE':'demo','SQLITE_PATH':':memory:'}); sys.exit(subprocess.run([sys.executable,'-m','pytest','tests/integration/','-v']).returncode)"

test-e2e:
	$(PYTHON) -c "import os,subprocess,sys; os.environ.update({'MODE':'demo','SQLITE_PATH':':memory:'}); sys.exit(subprocess.run([sys.executable,'-m','pytest','tests/e2e/','-v']).returncode)"

test-cov:
	$(PYTHON) -c "import os,subprocess,sys; os.environ.update({'MODE':'demo','SQLITE_PATH':':memory:'}); sys.exit(subprocess.run([sys.executable,'-m','pytest','tests/','--cov=repo_module','--cov-report=html','--cov-report=term']).returncode)"

# ─── Clean ───────────────────────────────────────────────────
clean:
	if exist repo_module.db del /f repo_module.db
	if exist demo_data\out\*.csv del /f demo_data\out\*.csv
	if exist demo_data\out\*.log del /f demo_data\out\*.log
	for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
	for /r . %%f in (*.pyc) do @del /f "%%f"
