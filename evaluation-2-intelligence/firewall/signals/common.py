from firewall.config import Config
from firewall.models import Reason


def reason(config: Config, code: str, severity: str, detail: str, weight: int | None = None) -> Reason:
    return Reason(code=code, severity=severity, detail=detail, weight=config.weights[code] if weight is None else weight)
