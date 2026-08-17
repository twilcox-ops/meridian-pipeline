import pytest
from sqlalchemy import create_engine

from pipeline import db


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    db.ensure_schema(eng)
    yield eng
    eng.dispose()
