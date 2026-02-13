"""百度网盘 OAuth2 认证模块"""

import time
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import requests
import yaml

AUTH_URL = "https://openapi.baidu.com/oauth/2.0/authorize"
TOKEN_URL = "https://openapi.baidu.com/oauth/2.0/token"
REDIRECT_URI = "oob"
PROJECT_DIR = Path(__file__).parent
CONFIG_PATH = PROJECT_DIR / "config.yaml"


def load_config():
    with open(str(CONFIG_PATH), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config):
    with open(str(CONFIG_PATH), "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


def get_auth_code(app_key: str) -> str:
    """通过浏览器获取授权码（oob 模式，用户手动粘贴）"""
    params = {
        "response_type": "code",
        "client_id": app_key,
        "redirect_uri": REDIRECT_URI,
        "scope": "basic,netdisk",
        "device_id": "baidu-netdisk-manager",
    }
    auth_url = f"{AUTH_URL}?{urlencode(params)}"

    print("正在打开浏览器进行授权...")
    print(f"如果浏览器未自动打开，请手动访问：\n{auth_url}\n")
    webbrowser.open(auth_url)

    print("授权后，页面会显示一个授权码。")
    code = input("请粘贴授权码: ").strip()
    return code


def exchange_token(app_key: str, secret_key: str, code: str) -> dict:
    """用授权码换取 access_token"""
    resp = requests.post(TOKEN_URL, params={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": app_key,
        "client_secret": secret_key,
        "redirect_uri": REDIRECT_URI,
    })
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"获取 token 失败: {data['error_description']}")
    return data


def refresh_access_token(app_key: str, secret_key: str, refresh_token: str) -> dict:
    """刷新 access_token"""
    resp = requests.post(TOKEN_URL, params={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": app_key,
        "client_secret": secret_key,
    })
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"刷新 token 失败: {data['error_description']}")
    return data


def ensure_token(config: dict) -> str:
    """确保 access_token 有效，必要时自动刷新"""
    auth = config.get("auth", {})
    access_token = auth.get("access_token", "")
    expires_at = auth.get("expires_at", 0)
    refresh_token = auth.get("refresh_token", "")

    if not access_token:
        raise RuntimeError("尚未授权，请先运行: python manager.py auth")

    # token 还有 5 分钟以上有效期
    if time.time() < expires_at - 300:
        return access_token

    if not refresh_token:
        raise RuntimeError("refresh_token 不存在，请重新授权: python manager.py auth")

    print("access_token 即将过期，正在刷新...")
    data = refresh_access_token(config["app_key"], config["secret_key"], refresh_token)
    _save_token_to_config(config, data)
    print("token 刷新成功。")
    return data["access_token"]


def do_auth(config: dict):
    """执行完整的授权流程"""
    app_key = config.get("app_key", "")
    secret_key = config.get("secret_key", "")

    if not app_key or not secret_key:
        print("错误：请先在 config.yaml 中配置 app_key 和 secret_key")
        print("获取方式：访问 https://pan.baidu.com/union 创建应用")
        return

    code = get_auth_code(app_key)
    if not code:
        print("未获取到授权码，授权流程取消。")
        return

    print(f"获取到授权码，正在换取 token...")
    data = exchange_token(app_key, secret_key, code)
    _save_token_to_config(config, data)
    print("授权成功！token 已保存到 config.yaml")


def _save_token_to_config(config: dict, token_data: dict):
    """将 token 数据保存到配置文件"""
    config.setdefault("auth", {})
    config["auth"]["access_token"] = token_data["access_token"]
    config["auth"]["refresh_token"] = token_data["refresh_token"]
    config["auth"]["expires_at"] = int(time.time()) + token_data["expires_in"]
    save_config(config)
