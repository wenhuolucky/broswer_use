from __future__ import annotations


class Job:
    def __init__(self, payload, result):
        self.payload = payload
        self.result = result


def test_published_job_data_normalizes_sohu_preview_url(monkeypatch):
    from app.api.routes import _published_job_data

    monkeypatch.setenv("SOHU_ACCOUNT_ID", "122702850")
    job = Job(
        payload={"user_id": "user1", "platform": "sohu", "title": "title", "cover_image_url": ""},
        result={"success": True, "article_url": "https://mp.sohu.com/h5/v2/newsPreview?id=1020931946&type=article"},
    )

    data = _published_job_data(job)

    assert data["article_url"] == "https://m.sohu.com/a/1020931946_122702850?sec=wd"


def test_published_job_data_keeps_toutiao_normalization():
    from app.api.routes import _published_job_data

    job = Job(
        payload={"user_id": "user1", "platform": "toutiao", "title": "title", "cover_image_url": ""},
        result={
            "success": True,
            "article_url": "https://mp.toutiao.com/profile_v4/graphic/preview?pgc_id=7649284280467587584",
        },
    )

    data = _published_job_data(job)

    assert data["article_url"] == "https://www.toutiao.com/article/7649284280467587584/?&source=m_redirect"
