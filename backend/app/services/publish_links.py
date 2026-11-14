"""发布链接的校验与"像不像这个平台"的判断。

系统的纪律是**不代发**（V6.0 任务书第⑦条）：人可以自己把片子发到平台，回来把链接
填进排期，系统只做两件事——把明显填错的东西挡回去，把"这条看着不像抖音"说出来。

为什么不用正则拦一切：平台会改域名、会有短链、会有地区站（``douyin.com`` / ``v.douyin.com``），
所以这里只拦两类**必然错**的东西（不是 http(s)、没有主机名），其余可疑的走提醒。
"""

from __future__ import annotations

from urllib.parse import urlsplit

MAX_URL_CHARS = 1000
ALLOWED_SCHEMES = ("http", "https")

#: 已知平台域名。查不到的平台（``other``）不做域名比对——不知道就别猜。
PLATFORM_HOSTS: dict[str, tuple[str, ...]] = {
    "douyin": ("douyin.com", "v.douyin.com", "iesdouyin.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "wechat": ("mp.weixin.qq.com", "weixin.qq.com"),
    "bilibili": ("bilibili.com", "b23.tv"),
    "video_account": ("channels.weixin.qq.com", "finder.video.qq.com"),
    "youtube": ("youtube.com", "youtu.be", "m.youtube.com"),
}

PLATFORM_LABELS: dict[str, str] = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "wechat": "公众号",
    "bilibili": "B 站",
    "video_account": "视频号",
    "youtube": "YouTube",
    "other": "其他平台",
}


class PublishLinkError(ValueError):
    """链接不合格。``reason`` 是稳定原因码，方便前端按码给提示。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - 打印用
        return self.message


def normalise_url(value: str) -> str:
    """去掉首尾空白并检查链接能不能放在网页上点开。

    只拒绝**必然错**的：不是 http/https、没有主机名、太长。
    """

    text = str(value or "").strip()
    if not text:
        raise PublishLinkError("empty_url", "标记为已发布必须给链接：没发就是没发")
    if len(text) > MAX_URL_CHARS:
        raise PublishLinkError(
            "too_long", f"链接超过 {MAX_URL_CHARS} 个字符：这不像一个发布页地址"
        )
    if any(char.isspace() for char in text):
        raise PublishLinkError("has_space", "链接里不能有空格：复制的时候可能只复制了一半")
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise PublishLinkError(
            "bad_scheme",
            f"链接必须以 http:// 或 https:// 开头（现在是以「{parts.scheme or '没有协议'}」开头）："
            "这个字段会当成链接放到页面上，别的写法点不开，也可能是别人塞进来的脚本",
        )
    if not parts.netloc:
        raise PublishLinkError(
            "no_host", "链接里没有主机名（形如 https://www.douyin.com/video/123）：检查是不是只贴了后半截"
        )
    return text


def host_of(value: str) -> str:
    """取主机名（小写、去掉端口）；解析不出来返回空串。"""

    try:
        return (urlsplit(str(value or "").strip()).hostname or "").lower()
    except ValueError:  # pragma: no cover - urlsplit 对坏输入抛 ValueError
        return ""


def looks_like_platform(value: str, platform: str) -> bool:
    """链接的主机名像不像这个平台。查不到域名表的平台一律返回 True（不知道就别猜）。"""

    hosts = PLATFORM_HOSTS.get(str(platform or "").strip().lower())
    if not hosts:
        return True
    host = host_of(value)
    if not host:
        return False
    return any(host == known or host.endswith(f".{known}") for known in hosts)


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(str(platform or "").strip().lower(), platform or "这个平台")


__all__ = [
    "ALLOWED_SCHEMES",
    "MAX_URL_CHARS",
    "PLATFORM_HOSTS",
    "PLATFORM_LABELS",
    "PublishLinkError",
    "host_of",
    "looks_like_platform",
    "normalise_url",
    "platform_label",
]
