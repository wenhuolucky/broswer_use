from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import accounts as accounts_route
from app.domain.channel import Channel, STATUS_CHANNEL_BOUND


@dataclass
class FakeAccountRow:
    id: int
    group_id: str
    group_text: str
    platform: str
    phone: str
    channel: str
    status: str = "normal"
    consecutive_failures: int = 0
    created_at: str = "2026-06-30T08:00:00+00:00"
    updated_at: str = "2026-06-30T08:00:00+00:00"


class FakeAccountStore:
    def __init__(self):
        self.rows: dict[tuple[str, str, str], FakeAccountRow] = {}
        self.next_id = 1

    def list_accounts(
        self,
        *,
        group_id: str,
        platform: str | None = None,
        status: str | None = None,
        available_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FakeAccountRow]:
        rows = [row for row in self.rows.values() if row.group_id == group_id]
        if platform:
            rows = [row for row in rows if row.platform == platform]
        if status:
            rows = [row for row in rows if row.status == status]
        if available_only:
            rows = [row for row in rows if row.status not in {"disabled", "muted"}]
        rows.sort(key=lambda row: row.id)
        return rows[offset : offset + limit]

    def get_account(self, *, group_id: str, platform: str, phone: str) -> FakeAccountRow | None:
        return self.rows.get((group_id, platform, phone))

    def upsert_account(
        self,
        *,
        group_id: str,
        group_text: str,
        platform: str,
        phone: str,
        channel: str,
        status: str,
        consecutive_failures: int,
        reset_failures: bool,
    ) -> FakeAccountRow:
        key = (group_id, platform, phone)
        row = self.rows.get(key)
        failures = 0 if reset_failures else consecutive_failures
        if row is None:
            row = FakeAccountRow(
                id=self.next_id,
                group_id=group_id,
                group_text=group_text,
                platform=platform,
                phone=phone,
                channel=channel,
                status=status,
                consecutive_failures=failures,
            )
            self.rows[key] = row
            self.next_id += 1
            return row
        row.group_text = group_text
        row.channel = channel
        row.status = status
        row.consecutive_failures = failures
        return row

    def update_account(
        self,
        *,
        group_id: str,
        platform: str,
        phone: str,
        group_text: str | None = None,
        new_phone: str | None = None,
        status: str | None = None,
        reset_failures: bool | None = None,
        consecutive_failures: int | None = None,
    ) -> FakeAccountRow | None:
        key = (group_id, platform, phone)
        row = self.rows.get(key)
        if row is None:
            return None
        if new_phone and new_phone != phone:
            new_key = (group_id, platform, new_phone)
            if new_key in self.rows:
                raise accounts_route.AccountConflict("account_phone_exists", "当前分组下该手机号已存在")
            self.rows.pop(key)
            row.phone = new_phone
            self.rows[new_key] = row
        if group_text is not None:
            row.group_text = group_text
        if status is not None:
            row.status = status
        if reset_failures is True:
            row.consecutive_failures = 0
        elif consecutive_failures is not None:
            row.consecutive_failures = consecutive_failures
        return row

    def delete_account(self, *, group_id: str, platform: str, phone: str) -> FakeAccountRow | None:
        return self.rows.pop((group_id, platform, phone), None)


class FakeAgent:
    def __init__(self):
        self.channels: dict[str, Channel] = {}
        self.publish_count_by_channel: dict[str, int] = {}
        self.deleted_channels: list[str] = []
        self.cancelled_jobs: list[str] = []

    def add_channel(self, channel_id: str, platform: str = "toutiao") -> Channel:
        channel = Channel(
            channel_id=channel_id,
            platform=platform,
            status=STATUS_CHANNEL_BOUND,
            account_name=f"{platform}-账号",
            cookie={"cookies": [{"name": "sessionid", "value": "ok"}], "origins": []},
        )
        self.channels[channel_id] = channel
        return channel

    def get_channel(self, channel_id: str):
        return self.channels.get(channel_id)

    def count_unfinished_publish_jobs_for_channel(self, channel_id: str) -> int:
        return self.publish_count_by_channel.get(channel_id, 0)

    def delete_channel(self, channel_id: str) -> bool:
        self.deleted_channels.append(channel_id)
        return self.channels.pop(channel_id, None) is not None

    async def cancel_job(self, job_id: str):
        self.cancelled_jobs.append(job_id)


def make_client(store: FakeAccountStore, agent: FakeAgent, monkeypatch) -> TestClient:
    monkeypatch.setattr(accounts_route, "account_store", store)
    monkeypatch.setattr(accounts_route, "agent", agent)
    app = FastAPI()
    app.include_router(accounts_route.router)
    return TestClient(app)


def seed_account(
    store: FakeAccountStore,
    agent: FakeAgent,
    *,
    group_id: str = "tenant-a",
    group_text: str = "Tenant A",
    platform: str = "toutiao",
    phone: str = "13800138000",
    channel_id: str = "channel-a",
    status: str = "normal",
    failures: int = 0,
) -> FakeAccountRow:
    agent.add_channel(channel_id, platform=platform)
    return store.upsert_account(
        group_id=group_id,
        group_text=group_text,
        platform=platform,
        phone=phone,
        channel=channel_id,
        status=status,
        consecutive_failures=failures,
        reset_failures=False,
    )


def test_accounts_all_is_scoped_by_request_group_id(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    seed_account(store, agent, group_id="tenant-a", phone="13800138000", channel_id="channel-a")
    seed_account(store, agent, group_id="tenant-b", phone="13900139000", channel_id="channel-b")
    client = make_client(store, agent, monkeypatch)

    response = client.get("/accounts/all?group_id=tenant-a")

    assert response.status_code == 200
    body = response.json()
    assert body["group_id"] == "tenant-a"
    assert body["count"] == 1
    assert body["accounts"][0]["phone"] == "13800138000"


def test_accounts_available_excludes_only_disabled_and_muted(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    seed_account(store, agent, phone="13800138000", channel_id="channel-a", status="normal")
    seed_account(store, agent, phone="13900139000", channel_id="channel-b", status="warning")
    seed_account(store, agent, phone="13700137000", channel_id="channel-c", status="disabled")
    seed_account(store, agent, phone="13600136000", channel_id="channel-d", status="muted")
    client = make_client(store, agent, monkeypatch)

    response = client.get("/accounts/available?group_id=tenant-a")

    assert response.status_code == 200
    phones = [account["phone"] for account in response.json()["accounts"]]
    assert phones == ["13800138000", "13900139000"]


def test_get_account_can_include_channel_and_runtime_status(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    seed_account(store, agent, channel_id="channel-a")
    agent.publish_count_by_channel["channel-a"] = 2
    client = make_client(store, agent, monkeypatch)

    response = client.get(
        "/accounts/toutiao/13800138000?group_id=tenant-a&include_channel=true&include_runtime=true"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["channel_id"] == "channel-a"
    assert body["channel"]["platform"] == "toutiao"
    assert body["runtime"] == {
        "account_status": "publishing",
        "publish_count": 2,
        "is_idle": False,
    }


def test_put_account_requires_group_id_and_validates_channel_platform(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    agent.add_channel("channel-a", platform="sohu")
    client = make_client(store, agent, monkeypatch)

    missing_group = client.put(
        "/accounts/toutiao/13800138000",
        json={"channel_id": "channel-a"},
    )
    mismatch = client.put(
        "/accounts/toutiao/13800138000",
        json={"group_id": "tenant-a", "channel_id": "channel-a"},
    )

    assert missing_group.status_code == 422
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "channel_platform_mismatch"


def test_blank_account_group_id_returns_stable_error_code(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    agent.add_channel("channel-a")
    client = make_client(store, agent, monkeypatch)

    response = client.put(
        "/accounts/toutiao/13800138000",
        json={"group_id": "", "channel_id": "channel-a"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "missing_group_id"


def test_put_account_upserts_only_within_request_group(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    agent.add_channel("channel-a")
    client = make_client(store, agent, monkeypatch)

    response = client.put(
        "/accounts/toutiao/13800138000",
        json={
            "group_id": "tenant-a",
            "group_text": "Tenant A",
            "channel_id": "channel-a",
            "status": "normal",
        },
    )

    assert response.status_code == 200
    assert response.json()["group_id"] == "tenant-a"
    assert response.json()["channel_id"] == "channel-a"
    assert store.get_account(group_id="tenant-b", platform="toutiao", phone="13800138000") is None


def test_patch_account_updates_phone_status_and_failures_within_group(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    seed_account(store, agent, failures=4)
    client = make_client(store, agent, monkeypatch)

    response = client.patch(
        "/accounts/toutiao/13800138000",
        json={
            "group_id": "tenant-a",
            "new_phone": "13900139000",
            "status": "disabled",
            "consecutive_failures": 5,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "13900139000"
    assert body["status"] == "disabled"
    assert body["consecutive_failures"] == 5
    assert store.get_account(group_id="tenant-a", platform="toutiao", phone="13800138000") is None


def test_patch_account_returns_404_outside_group_scope(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    seed_account(store, agent, group_id="tenant-a")
    client = make_client(store, agent, monkeypatch)

    response = client.patch(
        "/accounts/toutiao/13800138000",
        json={"group_id": "tenant-b", "status": "disabled"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "account_not_found"


def test_patch_account_ignores_blank_optional_text_fields_from_api_client(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    seed_account(store, agent, group_id="TianQW", group_text="测试组002", phone="19015896790", failures=3)
    client = make_client(store, agent, monkeypatch)

    response = client.patch(
        "/accounts/toutiao/19015896790",
        json={
            "group_id": "TianQW",
            "group_text": "测试组002",
            "new_phone": "",
            "status": "",
            "reset_failures": True,
            "consecutive_failures": 0,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "19015896790"
    assert body["status"] == "normal"
    assert body["consecutive_failures"] == 0


def test_delete_account_refuses_busy_channel_without_force(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    seed_account(store, agent, channel_id="channel-a")
    agent.publish_count_by_channel["channel-a"] = 1
    client = make_client(store, agent, monkeypatch)

    response = client.delete("/accounts/toutiao/13800138000?group_id=tenant-a")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_busy"
    assert store.get_account(group_id="tenant-a", platform="toutiao", phone="13800138000") is not None


def test_delete_account_force_removes_account_and_channel(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    seed_account(store, agent, channel_id="channel-a")
    client = make_client(store, agent, monkeypatch)

    response = client.delete("/accounts/toutiao/13800138000?group_id=tenant-a&force=true")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["channel_deleted"] is True
    assert body["channel_id"] == "channel-a"
    assert "channel-a" in agent.deleted_channels
    assert store.get_account(group_id="tenant-a", platform="toutiao", phone="13800138000") is None


def test_invalid_phone_returns_stable_error_code(monkeypatch):
    client = make_client(FakeAccountStore(), FakeAgent(), monkeypatch)

    response = client.get("/accounts/toutiao/not-a-phone?group_id=tenant-a")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_phone"


def test_account_platform_parameters_are_openapi_enums(monkeypatch):
    client = make_client(FakeAccountStore(), FakeAgent(), monkeypatch)

    schema = client.get("/openapi.json").json()
    account_path = schema["paths"]["/accounts/{platform}/{phone}"]
    get_parameters = account_path["get"]["parameters"]
    path_platform = next(param for param in get_parameters if param["name"] == "platform")
    assert path_platform["schema"]["enum"] == ["toutiao", "sohu"]

    list_parameters = schema["paths"]["/accounts/all"]["get"]["parameters"]
    query_platform = next(param for param in list_parameters if param["name"] == "platform")
    assert query_platform["schema"]["anyOf"][0]["enum"] == ["toutiao", "sohu"]


def test_account_path_rejects_unsupported_platform_before_store_lookup(monkeypatch):
    store = FakeAccountStore()
    agent = FakeAgent()
    seed_account(store, agent, platform="toutiao", phone="13800138000")
    client = make_client(store, agent, monkeypatch)

    response = client.get("/accounts/wechat/13800138000?group_id=tenant-a")

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", "platform"]


def test_account_openapi_contains_chinese_scalar_descriptions(monkeypatch):
    client = make_client(FakeAccountStore(), FakeAgent(), monkeypatch)

    schema = client.get("/openapi.json").json()
    all_operation = schema["paths"]["/accounts/all"]["get"]
    put_operation = schema["paths"]["/accounts/{platform}/{phone}"]["put"]
    patch_schema = schema["components"]["schemas"]["AccountPatchRequest"]
    upsert_schema = schema["components"]["schemas"]["AccountUpsertRequest"]
    response_schema = schema["components"]["schemas"]["AccountResponse"]

    assert all_operation["summary"] == "列出所有账号"
    assert "group_id 必填" in all_operation["description"]
    assert put_operation["summary"] == "保存或重新绑定账号"
    assert "channel_id 必须已存在" in put_operation["description"]

    put_params = {param["name"]: param for param in put_operation["parameters"]}
    assert "枚举值：toutiao、sohu" in put_params["platform"]["description"]
    assert "11 位手机号" in put_params["phone"]["description"]

    all_params = {param["name"]: param for param in all_operation["parameters"]}
    assert "调用方传入" in all_params["group_id"]["description"]
    assert "status 不是 disabled 且不是 muted" in all_params["status"]["description"]
    assert all_params["limit"]["schema"]["maximum"] == 500

    assert "必填" in upsert_schema["properties"]["group_id"]["description"]
    assert upsert_schema["properties"]["status"]["enum"] == ["normal", "warning", "muted", "disabled"]
    assert "空字符串按未提供处理" in patch_schema["properties"]["status"]["description"]
    assert response_schema["properties"]["platform"]["description"].startswith("平台枚举")


def test_account_group_id_is_required_without_default_in_openapi(monkeypatch):
    client = make_client(FakeAccountStore(), FakeAgent(), monkeypatch)

    schema = client.get("/openapi.json").json()
    all_params = {
        param["name"]: param
        for param in schema["paths"]["/accounts/all"]["get"]["parameters"]
    }
    available_params = {
        param["name"]: param
        for param in schema["paths"]["/accounts/available"]["get"]["parameters"]
    }
    detail_params = {
        param["name"]: param
        for param in schema["paths"]["/accounts/{platform}/{phone}"]["get"]["parameters"]
    }
    delete_params = {
        param["name"]: param
        for param in schema["paths"]["/accounts/{platform}/{phone}"]["delete"]["parameters"]
    }
    upsert_schema = schema["components"]["schemas"]["AccountUpsertRequest"]
    patch_schema = schema["components"]["schemas"]["AccountPatchRequest"]

    for params in (all_params, available_params, detail_params, delete_params):
        assert params["group_id"]["required"] is True
        assert "default" not in params["group_id"]["schema"]

    assert "group_id" in upsert_schema["required"]
    assert "default" not in upsert_schema["properties"]["group_id"]
    assert "group_id" in patch_schema["required"]
    assert "default" not in patch_schema["properties"]["group_id"]
