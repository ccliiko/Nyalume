"""测试前把 MEMORY_DB 指到临时目录，避免污染真实 agent.db。"""

import os
import shutil
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="nyalume_test_")
os.environ["MEMORY_DB"] = os.path.join(_TMP_DIR, "test.db")
os.environ["AGENT_WORKSPACE"] = os.path.join(_TMP_DIR, "workspace")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
