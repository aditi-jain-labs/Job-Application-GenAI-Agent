#!/bin/zsh
# Opens the job agent dashboard at http://localhost:8501 (starts it first if it is not running).
cd "${0:A:h}"      # the real folder, also when started from a Desktop shortcut (symlink)
# The agent runs in your usual python3; the dashboard has its own virtual environment in .dashboard-venv
if [ ! -x .dashboard-venv/bin/streamlit ]; then
  echo "The dashboard is not installed yet. Run: python3 -m venv .dashboard-venv && .dashboard-venv/bin/pip install streamlit ruamel.yaml pandas pyyaml python-dotenv"
  exit 1
fi
export JOB_AGENT_PYTHON="${JOB_AGENT_PYTHON:-$(command -v python3)}"
URL="http://localhost:8501"
if curl -s -o /dev/null "$URL/_stcore/health"; then
  open "$URL"
  exit 0
fi
(sleep 3; open "$URL") &
exec .dashboard-venv/bin/streamlit run dashboard.py --server.port 8501 --server.address localhost \
  --server.headless true --browser.gatherUsageStats false
