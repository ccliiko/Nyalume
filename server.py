"""启动入口：python server.py，然后访问 http://127.0.0.1:8000"""

import uvicorn

from mini_agent.frontends.web.server import app


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
