"""部署静态检查：受控出网方案不得放开内部网络；默认部署仍然完全离线。只读文件，不联网、不需要 Docker。"""
import re

import yaml

from app.settings import ROOT


def load(name):
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def merged():
    """docker-compose.yml + docker-compose.llm.yml 的合并结果（只合并本测试关心的键）。"""
    base, llm = load("docker-compose.yml"), load("docker-compose.llm.yml")
    nets = {**base["networks"], **llm["networks"]}
    svc = {k: dict(v) for k, v in base["services"].items()}
    for k, v in llm["services"].items():
        svc.setdefault(k, {}).update({kk: vv for kk, vv in v.items() if kk != "environment"})
        svc[k]["environment"] = {**svc[k].get("environment", {}), **v.get("environment", {})}
    return svc, nets, base, llm


def test_default_deployment_stays_offline():
    base = load("docker-compose.yml")
    assert base["networks"]["backend"]["internal"] is True
    assert base["services"]["api"]["environment"]["LLM_PROVIDER"] == "deterministic"
    assert "egress" not in base["services"] and "egress" not in base["networks"]
    assert not any("HTTPS_PROXY" in s.get("environment", {}) for s in base["services"].values())


def test_only_the_egress_service_can_reach_the_internet():
    svc, nets, _, _ = merged()
    assert nets["backend"]["internal"] is True                           # 内部网络没有被放开
    assert not nets["egress"] or not nets["egress"].get("internal")
    on_egress = {name for name, s in svc.items() if "egress" in s.get("networks", [])}
    assert on_egress == {"egress"}                                       # api / db 不在可出网的网络里
    assert "backend" in svc["egress"]["networks"]
    for name in ("api", "db"):
        assert "ports" not in svc[name]                                  # 仍没有宿主机端口


def test_api_uses_the_proxy_and_requires_an_explicit_key():
    svc, _, _, llm = merged()
    env = svc["api"]["environment"]
    assert env["HTTPS_PROXY"] == "http://egress:3128" and "db" in env["NO_PROXY"]
    assert env["LLM_PROVIDER"] == "deepseek" and env["LLM_BASE_URL"] == "https://api.deepseek.com"
    assert env["LLM_API_KEY"].startswith("${LLM_API_KEY:?")              # 缺失即报错，不静默回退
    assert "LLM_API_KEY" not in str(svc["egress"].get("environment", {})) and not svc["egress"].get("environment")   # 代理拿不到密钥


def test_proxy_config_allows_exactly_one_https_host_and_denies_the_rest():
    lines = [l.strip() for l in (ROOT / "deploy" / "squid.conf").read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    acl_hosts = [l for l in lines if l.startswith("acl ") and " dstdomain " in l]
    assert acl_hosts == ["acl llm_host dstdomain api.deepseek.com"]      # 只有一个目的域名，没有通配符
    rules = [l for l in lines if l.startswith("http_access")]
    assert rules == ["http_access deny !tunnel", "http_access deny !tls_port", "http_access allow llm_host", "http_access deny all"]
    assert "acl tls_port port 443" in lines and "acl tunnel method CONNECT" in lines
    assert "cache deny all" in lines
    assert not re.search(r"^acl\s+\w+\s+(src|dst)\s", "\n".join(lines), re.M)


def test_proxy_host_matches_the_configured_llm_base_url():
    host = re.search(r"^acl \w+ dstdomain (\S+)", (ROOT / "deploy" / "squid.conf").read_text(encoding="utf-8"), re.M).group(1)
    assert load("docker-compose.llm.yml")["services"]["api"]["environment"]["LLM_BASE_URL"] == f"https://{host}"


def test_egress_container_is_locked_down():
    egress = load("docker-compose.llm.yml")["services"]["egress"]
    assert egress["read_only"] is True and egress["cap_drop"] == ["ALL"] and "no-new-privileges:true" in egress["security_opt"]
    assert egress["image"].split(":")[1] != "latest"                      # 固定版本
