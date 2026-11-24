import os
import shutil
import pytest

@pytest.fixture(autouse=True)
def isolate_test_data(tmp_path, monkeypatch):
    """
    Ensure all tests use an isolated temporary directory for DATA_DIR:
    - audit.log
    - memory files
    - ingested logs
    - incident stores and reports
    """
    test_data_dir = str(tmp_path / "data")
    os.makedirs(test_data_dir, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", test_data_dir)
    yield test_data_dir
