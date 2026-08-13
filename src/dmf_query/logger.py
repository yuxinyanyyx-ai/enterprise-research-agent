import logging

from config import LOG_DIR


def setup_logging():
    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_file = LOG_DIR / "dmf_query.log"

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s - "
            "%(message)s"
        ),
        handlers=[
            logging.StreamHandler(),

            logging.FileHandler(
                log_file,
                encoding="utf-8",
            ),
        ],
    )