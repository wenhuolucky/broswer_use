from __future__ import annotations

from app.server import app


def test_business_openapi_operations_use_chinese_scalar_metadata():
    schema = app.openapi()
    expected = {
        ("/api/v1/platforms", "get"): "列出支持的平台",
        ("/api/v1/jobs", "post"): "创建发文任务",
        ("/api/v1/jobs/{job_id}", "get"): "查询发文任务",
        ("/api/v1/jobs/{job_id}/save-cookie", "post"): "保存发文任务 Cookie",
        ("/api/v1/jobs/{job_id}/cancel", "post"): "取消发文任务",
        ("/api/v1/login-sessions", "post"): "创建登录会话",
        ("/api/v1/login-sessions/{session_id}", "get"): "查询登录会话",
        ("/api/v1/login-sessions/{session_id}", "delete"): "取消登录会话",
        ("/api/v1/channels/{channel_id}", "get"): "查询渠道详情",
        ("/api/v1/channels/{channel_id}/publish-status", "get"): "查询渠道发文状态",
        ("/api/v1/channels/{channel_id}", "delete"): "删除渠道",
    }

    for (path, method), summary in expected.items():
        operation = schema["paths"][path][method]
        assert operation["summary"] == summary
        assert operation["description"]
        assert any("\u4e00" <= char <= "\u9fff" for char in operation["description"])


def test_business_path_parameters_have_chinese_descriptions():
    schema = app.openapi()
    targets = [
        ("/api/v1/jobs/{job_id}", "get", "job_id", "发文任务 ID"),
        ("/api/v1/jobs/{job_id}/save-cookie", "post", "job_id", "发文任务 ID"),
        ("/api/v1/jobs/{job_id}/cancel", "post", "job_id", "发文任务 ID"),
        ("/api/v1/login-sessions/{session_id}", "get", "session_id", "登录会话 ID"),
        ("/api/v1/login-sessions/{session_id}", "delete", "session_id", "登录会话 ID"),
        ("/api/v1/channels/{channel_id}", "get", "channel_id", "渠道 ID"),
        ("/api/v1/channels/{channel_id}/publish-status", "get", "channel_id", "渠道 ID"),
        ("/api/v1/channels/{channel_id}", "delete", "channel_id", "渠道 ID"),
    ]

    for path, method, name, phrase in targets:
        parameters = {
            parameter["name"]: parameter
            for parameter in schema["paths"][path][method]["parameters"]
        }
        assert phrase in parameters[name]["description"]


def test_request_and_response_schemas_explain_required_fields_and_enums_in_chinese():
    schema = app.openapi()
    schemas = schema["components"]["schemas"]

    publish_request = schemas["AutoPublishRequest"]
    login_request = schemas["LoginRequest"]
    job_response = schemas["JobResponse"]
    login_response = schemas["LoginSessionResponse"]
    channel_response = schemas["ChannelResponse"]
    platform_info = schemas["PlatformInfo"]

    assert publish_request["required"] == ["channel_id", "title", "content"]
    assert "必填" in publish_request["properties"]["channel_id"]["description"]
    assert "必填" in publish_request["properties"]["title"]["description"]
    assert "可选" in publish_request["properties"]["cover_image_url"]["description"]

    assert login_request["properties"]["platform"]["enum"] == ["toutiao", "sohu"]
    assert "枚举值：toutiao、sohu" in login_request["properties"]["platform"]["description"]
    assert "默认 toutiao" in login_request["properties"]["platform"]["description"]

    assert "queued" in job_response["properties"]["status"]["description"]
    assert "succeeded" in job_response["properties"]["status"]["description"]
    assert "状态相关" in job_response["properties"]["live_url"]["description"]
    assert "枚举值：toutiao、sohu" in login_response["properties"]["platform"]["description"]
    assert "pending" in channel_response["properties"]["status"]["description"]
    assert "枚举值：toutiao、sohu" in platform_info["properties"]["id"]["description"]


def test_create_job_status_docs_use_actual_cookie_status_names():
    schema = app.openapi()
    description = schema["components"]["schemas"]["JobCreatedResponse"]["properties"]["status"]["description"]

    assert "checking_cookie" in description
    assert "waiting_cookie" in description
    assert "check_cookie" not in description
    assert "wait_cookie" not in description


def test_service_openapi_title_tags_and_health_are_chinese_friendly():
    schema = app.openapi()

    assert "浏览器发文服务" in schema["info"]["title"]
    assert "自动发文" in schema["info"]["description"]
    assert any(
        tag["name"] == "login-sessions" and "登录会话" in tag["description"]
        for tag in schema.get("tags", [])
    )

    health = schema["paths"]["/health"]["get"]
    assert health["summary"] == "健康检查"
    assert "存活探针" in health["description"]
