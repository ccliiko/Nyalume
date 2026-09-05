"""测试前把 MEMORY_DB 指到临时目录，避免污染真实 agent.db。"""

import os
import shutil
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="mini_agent_test_")
os.environ["MEMORY_DB"] = os.path.join(_TMP_DIR, "test.db")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
