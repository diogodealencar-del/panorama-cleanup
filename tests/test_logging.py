import logging

from panorama_cleanup.logging_setup import LOGGER_NAME, configure_logging


def test_log_written_to_logs_dir(project):
    log_file = configure_logging(project, "20261103T163000Z-inventory-abababab")
    logging.getLogger(LOGGER_NAME).info("teste de log")
    for handler in logging.getLogger(LOGGER_NAME).handlers:
        handler.flush()
    assert log_file.parent == project.logs_dir
    assert "teste de log" in log_file.read_text(encoding="utf-8")
