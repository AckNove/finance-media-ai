"""服务商配置指引：缺哪个变量、写在哪、写完怎么验。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.services import provider_guide as guide


def _providers(**overrides) -> dict[str, dict[str, object]]:
    base: dict[str, dict[str, object]] = {
        "deepseek": {
            "display_name": "DeepSeek",
            "enabled": True,
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url_env": "DEEPSEEK_BASE_URL",
            "status": "ready",
            "message": "",
            "base_url": "https://api.deepseek.com/v1",
        },
        "openai": {
            "display_name": "OpenAI",
            "enabled": False,
            "api_key_env": "OPENAI_API_KEY",
            "base_url_env": "OPENAI_BASE_URL",
            "status": "provider_disabled",
            "message": "provider openai 未启用",
        },
        "local": {
            "display_name": "本地模型",
            "enabled": False,
            "api_key_env": None,
            "base_url_env": "LOCAL_MODEL_BASE_URL",
            "status": "provider_disabled",
            "message": "provider local 未启用",
        },
    }
    for name, patch in overrides.items():
        base[name] = {**base.get(name, {}), **patch}
    return base


def test_env_file_keys_reads_names_only(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 注释行\n"
        "DEEPSEEK_API_KEY=sk-should-never-be-echoed\n"
        "export OPENAI_API_KEY=abc\n"
        "\n"
        "NO_EQUALS_SIGN\n"
        "DEEPSEEK_API_KEY=重复的只算一次\n",
        encoding="utf-8",
    )
    names = guide.env_file_keys(env_file)
    assert names == ("DEEPSEEK_API_KEY", "OPENAI_API_KEY")
    # 值一个字符都不能出现
    assert not any("sk-" in name for name in names)


def test_env_file_keys_tolerates_a_missing_file(tmp_path: Path) -> None:
    assert guide.env_file_keys(tmp_path / "nope.env") == ()


def test_key_in_process_and_in_file_are_reported_separately(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=written-but-not-loaded\n", encoding="utf-8")
    items = guide.guide_of(_providers(), environ={}, env_file=env_file)
    deepseek = next(item for item in items if item.name == "deepseek")
    # 写进 .env 但进程没读 —— 必须能分辨，否则人会以为改完就生效了
    assert deepseek.key_in_env_file is True
    assert deepseek.key_in_process is False
    assert any("重启" in step for step in deepseek.steps)
    assert deepseek.env_file.endswith(".env")
    assert deepseek.env_file_exists is True


def test_missing_key_tells_where_to_write_it(tmp_path: Path) -> None:
    items = guide.guide_of(_providers(), environ={}, env_file=tmp_path / ".env")
    deepseek = next(item for item in items if item.name == "deepseek")
    assert deepseek.key_in_process is False and deepseek.key_in_env_file is False
    joined = " ".join(deepseek.steps)
    assert "DEEPSEEK_API_KEY" in joined
    assert ".env" in joined
    assert ".gitignore" in joined  # 说明为什么写在这里是安全的
    # 未启用的服务商必须先说「页面不改配置，得你自己改」
    openai = next(item for item in items if item.name == "openai")
    assert "页面不改配置" in " ".join(openai.steps)


def test_a_provider_without_models_gets_an_honest_command(tmp_path: Path) -> None:
    items = guide.guide_of(_providers(), environ={}, env_file=tmp_path / ".env")
    openai = next(item for item in items if item.name == "openai")
    # 不能给出「--provider openai --model deepseek-flash」这种照着跑必然失败的命令
    assert openai.verify_command == "python -m backend.scripts.model_smoke --provider openai"
    assert "还没有启用的模型" in openai.steps[0]


def test_local_provider_says_it_needs_no_key(tmp_path: Path) -> None:
    items = guide.guide_of(_providers(), environ={}, env_file=tmp_path / ".env")
    local = next(item for item in items if item.name == "local")
    assert any("不需要密钥" in step for step in local.steps)
    assert local.base_url_env == "LOCAL_MODEL_BASE_URL"


def test_usable_providers_come_first(tmp_path: Path) -> None:
    items = guide.guide_of(_providers(), environ={"DEEPSEEK_API_KEY": "x"}, env_file=tmp_path / ".env")
    assert items[0].name == "deepseek"
    assert items[0].ready is True
    assert [item.name for item in items[1:]] == ["local", "openai"] or [item.name for item in items[1:]] == ["openai", "local"]


def test_summary_counts_and_wording(tmp_path: Path) -> None:
    empty = guide.summary_of([], env_file=tmp_path / ".env")
    assert "还没有登记任何服务商" in empty["note"]

    none_ready = guide.summary_of(
        guide.guide_of(_providers(deepseek={"status": "missing_api_key"}), environ={}, env_file=tmp_path / ".env")
    )
    assert "一个服务商都用不了" in none_ready["note"]

    one_ready = guide.summary_of(guide.guide_of(_providers(), environ={"DEEPSEEK_API_KEY": "x"}, env_file=tmp_path / ".env"))
    # 1 个可用、另外两个未启用：措辞不能是「1 个都可用」
    assert "只有 1 个服务商可用" in one_ready["note"]
    assert one_ready["restart_hint"]
    assert one_ready["env_file"].endswith(".env")


def test_guide_never_echoes_a_secret(tmp_path: Path) -> None:
    # 拼出来的假密钥：字面量里不出现 sk- 前缀，免得被"提交前扫密钥"误报
    secret = "sk-" + "abcdefghijklmnopqrstuvwxyz012345"
    env_file = tmp_path / ".env"
    env_file.write_text(f"DEEPSEEK_API_KEY={secret}\n", encoding="utf-8")
    payload = guide.summary_of(guide.guide_of(_providers(), environ={"DEEPSEEK_API_KEY": secret}, env_file=env_file))
    rendered = repr(payload)
    assert secret not in rendered
    assert "sk-" not in rendered


def test_steps_have_no_markdown_emphasis(tmp_path: Path) -> None:
    """页面上这些步骤是纯文本直出：写 `**` 会原样显示成两个星号。"""

    items = guide.guide_of(_providers(), environ={}, env_file=tmp_path / ".env")
    for item in items:
        for step in item.steps:
            assert "**" not in step, step


def test_api_returns_the_guide(client: TestClient) -> None:
    response = client.get("/api/system/provider-guide")
    assert response.status_code == 200
    body = response.json()
    assert body["restart_hint"]
    assert body["items"], "至少要列出配置里登记的服务商"
    for item in body["items"]:
        assert item["name"]
        assert isinstance(item["steps"], list)
        assert "sk-" not in repr(item), "接口不许回显密钥"
