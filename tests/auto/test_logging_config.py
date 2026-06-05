from auto.logging_config import setup_job_logger


def test_setup_job_logger_creates_only_per_job_log(tmp_path, capsys):
    logger, log_path = setup_job_logger("job-unique-log", tmp_path)

    logger.info("hello")

    assert log_path == tmp_path / "jobs" / "job-unique-log.log"
    assert log_path.exists()
    assert list(tmp_path.glob("auto_service_*.log")) == []
    captured = capsys.readouterr()
    assert "job-unique-log" in captured.out
    assert "hello" in captured.out
