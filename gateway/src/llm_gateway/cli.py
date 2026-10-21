import logging

import uvicorn

from .app import create_app
from .config import Settings


def main():
    config = Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run(create_app(config), host=config.host, port=config.port, access_log=False)


if __name__ == "__main__":
    main()
