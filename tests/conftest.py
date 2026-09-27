import pytest

from app.services import llm


@pytest.fixture(autouse=True)
def _reset_llm_stream():
    """流式开关是进程级状态，每个测试前后复位。"""
    llm._use_stream = llm.STREAM_MODE == "true"
    yield
    llm._use_stream = llm.STREAM_MODE == "true"
