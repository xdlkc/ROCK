"""
Egress Gateway 服务入口。

启动方式：
  egress --port 18080 --config /etc/rock/rock-local.yml

依赖 mitmproxy（rl-rock[egress] 可选依赖组）。
mitmproxy 以透明代理模式（mode=transparent）监听，
通过宿主机 iptables 规则将 sandbox 出站 HTTP/HTTPS 重定向至此端口。
"""

from __future__ import annotations

import argparse
import asyncio

from rock.logger import init_logger

logger = init_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="ROCK Egress Gateway")
    parser.add_argument("--port", type=int, default=18080, help="透明代理监听端口")
    parser.add_argument("--config", type=str, default=None, help="RockConfig YAML 路径")
    args = parser.parse_args()

    try:
        from rock.egress._runner import run_gateway
    except ImportError as e:
        logger.error(
            f"Egress Gateway 启动失败：缺少依赖 {e}。"
            "请安装 rl-rock[egress]（包含 mitmproxy）后重试。"
        )
        raise SystemExit(1)

    asyncio.run(run_gateway(port=args.port, config_path=args.config))
