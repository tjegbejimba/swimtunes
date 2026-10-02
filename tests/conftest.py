import shutil
from pathlib import Path

import pytest


@pytest.fixture
def artifact_dir(request):
    root = Path(__file__).parent / ".artifacts" / request.node.name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)
