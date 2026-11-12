import uuid

import pytest

OWNER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def request_dict():
    return {
        "job_id": str(uuid.uuid4()),
        "user_id": OWNER,
        "report_date": "2026-11-12",
        "timezone": "Asia/Taipei",
        "locale": "zh-TW",
        "topics": [
            {"topic_id": str(uuid.uuid4()), "title": "AI", "item_count": 3},
            {"topic_id": str(uuid.uuid4()), "title": "Weather", "item_count": 1},
        ],
    }
