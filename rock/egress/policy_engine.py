from rock.config import EgressGatewayConfig, EgressPolicyRule


class PolicyEngine:
    """
    按规则优先级（sandbox > user/exp/ns > route > global）解析审计模式，
    并检查 allow/deny 访问控制。
    """

    def __init__(self, config: EgressGatewayConfig) -> None:
        self._config = config

    def resolve_mode(
        self,
        sandbox_id: str,
        user_id: str,
        experiment_id: str,
        namespace: str,
        url: str,
    ) -> str:
        """返回当前请求应使用的审计模式（off / metadata-only / full-capture）。"""
        rules = self._config.mode.rules
        default = self._config.mode.default

        # 优先级 1：sandbox 级（一票否决）
        for rule in rules:
            if rule.sandbox_id and rule.sandbox_id == sandbox_id:
                return rule.mode

        # 优先级 2：user/experiment/namespace 级（先匹配先生效）
        for rule in rules:
            if rule.sandbox_id:
                continue  # 跳过 sandbox 级规则
            if rule.route_prefix:
                continue  # 跳过 route 级规则
            if self._match_identity(rule, user_id, experiment_id, namespace):
                return rule.mode

        # 优先级 3：route 级（URL 前缀匹配）
        for rule in rules:
            if rule.route_prefix and url.startswith(rule.route_prefix):
                return rule.mode

        return default

    def check_access(self, host: str, port: int) -> str:
        """返回 'allow' 或 'deny'。"""
        policy = self._config.policy
        target = f"{host}:{port}"

        # deny 列表优先
        for denied in policy.deny_hosts:
            if denied == target or denied == host:
                return "deny"

        # allow 列表
        for allowed in policy.allow_hosts:
            if allowed == target or allowed == host:
                return "allow"

        return policy.default_action

    @staticmethod
    def _match_identity(rule: EgressPolicyRule, user_id: str, experiment_id: str, namespace: str) -> bool:
        """
        规则命中条件：所有**已声明**（非空）的维度都必须与请求中的值匹配。
        至少有一个维度被声明，才构成有效规则。
        """
        has_any = bool(rule.user_id or rule.experiment_id or rule.namespace)
        if not has_any:
            return False
        if rule.user_id and rule.user_id != user_id:
            return False
        if rule.experiment_id and rule.experiment_id != experiment_id:
            return False
        if rule.namespace and rule.namespace != namespace:
            return False
        return True
