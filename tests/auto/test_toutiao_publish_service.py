import asyncio

from auto.adapters.toutiao_publish_service import AutoToutiaoPublishService


async def fast_sleep(_seconds):
    return None


class PageStub:
    def __init__(self, values):
        self.values = list(values)
        self.goto_urls = []

    async def goto(self, url):
        self.goto_urls.append(url)

    async def evaluate(self, script, *args):
        if not self.values:
            return None
        value = self.values.pop(0)
        if callable(value):
            return value(script, *args)
        return value


class SessionStub:
    def __init__(self, page, urls):
        self.page = page
        self.urls = list(urls)

    async def get_current_page(self):
        return self.page

    async def get_current_page_url(self):
        if len(self.urls) > 1:
            return self.urls.pop(0)
        return self.urls[0]


def test_open_latest_article_reads_article_card_image_href_and_verifies_detail_title():
    service = AutoToutiaoPublishService()
    page = PageStub([
        1,
        [{
            "href": "https://www.toutiao.com/item/7646246126252835364/",
            "text": "你好啊",
        }],
        "你好啊",
    ])
    session = SessionStub(
        page,
        [
            "https://mp.toutiao.com/profile_v4/graphic/articles",
            "https://www.toutiao.com/item/7646246126252835364/",
        ],
    )

    result = asyncio.run(service._open_latest_article_from_articles_page(session, "你好啊", None))

    assert page.goto_urls == [
        "https://mp.toutiao.com/profile_v4/graphic/articles",
        "https://www.toutiao.com/item/7646246126252835364/",
    ]
    assert result == {
        "matched": True,
        "article_url": "https://www.toutiao.com/item/7646246126252835364/",
        "detail_title": "你好啊",
    }


def test_open_latest_article_retries_until_matching_title_candidate_appears(monkeypatch):
    monkeypatch.setattr(
        "auto.adapters.toutiao_publish_service.asyncio.sleep",
        fast_sleep,
    )
    service = AutoToutiaoPublishService()
    page = PageStub([
        1,
        [{
            "href": "https://www.toutiao.com/item/old/",
            "text": "旧文章",
        }],
        1,
        [{
            "href": "https://www.toutiao.com/item/new/",
            "text": "你好啊002\n06-05 09:50\n已发布",
        }],
        "你好啊002",
    ])
    session = SessionStub(
        page,
        [
            "https://mp.toutiao.com/profile_v4/graphic/articles",
            "https://www.toutiao.com/item/new/",
        ],
    )

    result = asyncio.run(service._open_latest_article_from_articles_page(session, "你好啊002", None))

    assert page.goto_urls == [
        "https://mp.toutiao.com/profile_v4/graphic/articles",
        "https://mp.toutiao.com/profile_v4/graphic/articles",
        "https://www.toutiao.com/item/new/",
    ]
    assert result == {
        "matched": True,
        "article_url": "https://www.toutiao.com/item/new/",
        "detail_title": "你好啊002",
    }


def test_open_latest_article_does_not_open_first_candidate_when_title_never_matches(monkeypatch):
    monkeypatch.setattr(
        "auto.adapters.toutiao_publish_service.asyncio.sleep",
        fast_sleep,
    )
    service = AutoToutiaoPublishService()
    service.article_lookup_attempts = 1
    page = PageStub([
        1,
        [{
            "href": "https://www.toutiao.com/item/old/",
            "text": "旧文章",
        }],
    ])
    session = SessionStub(
        page,
        ["https://mp.toutiao.com/profile_v4/graphic/articles"],
    )

    result = asyncio.run(service._open_latest_article_from_articles_page(session, "你好啊002", None))

    assert page.goto_urls == ["https://mp.toutiao.com/profile_v4/graphic/articles"]
    assert result == {"matched": False, "article_url": "", "detail_title": ""}
