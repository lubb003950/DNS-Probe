from packages.alerts.yunzhi import build_payload, split_contacts


def test_split_contacts() -> None:
    warner, telephone = split_contacts("张三:13800000000,李四:13900000000")
    assert warner == "张三,李四"
    assert telephone == "13800000000,13900000000"


def test_build_payload() -> None:
    payload = build_payload(
        targetname="api.example.com",
        targetip="10.0.0.53",
        level="Critical",
        check="最近5分钟失败率>30%",
        description="test",
        customerip="10.0.0.10",
        contacts="张三:13800000000",
        system_name="DNS探测系统",
        app_name="DNS探测引擎",
    )
    assert payload["targetname"] == "api.example.com"
    assert payload["level"] == "Critical"
    assert payload["warner"] == "张三"
    assert payload["telephone"] == "13800000000"
