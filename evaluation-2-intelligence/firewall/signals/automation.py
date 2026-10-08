from __future__ import annotations

from firewall.config import Config
from firewall.index_keys import candidate_identity_key, normalize_email, normalize_phone, template_text
from firewall.models import Application, Reason
from firewall.signals.common import reason
from firewall.store import ApplicationStore


def detect_automation(application: Application, store: ApplicationStore, config: Config) -> list[Reason]:
    submitted_at = application.signals.submitted_at
    start = submitted_at - config.velocity_window_seconds
    device_count = 1 + len(store.recent_by_device(application.signals.device_id, start, submitted_at))
    ip_count = 1 + len(store.recent_by_ip(application.signals.ip, start, submitted_at))
    email = normalize_email(application.candidate.email)
    phone = normalize_phone(application.candidate.phone)
    identity_count = 1 + len(store.recent_by_identity(email, phone, start, submitted_at))

    found: list[Reason] = []
    device_burst = device_count > config.velocity_limit
    identity_burst = identity_count > config.identity_velocity_limit
    ip_burst = ip_count > config.velocity_limit
    if device_burst or identity_burst:
        sources = []
        if device_burst:
            sources.append(f"device={device_count}")
        if ip_burst:
            sources.append(f"ip={ip_count}")
        if identity_burst:
            sources.append(f"identity={identity_count}")
        found.append(
            reason(
                config,
                "VELOCITY_HIGH",
                "high",
                f"Application count in {config.velocity_window_seconds}s exceeded the limit ({', '.join(sources)}).",
            )
        )
    elif ip_burst:
        found.append(
            reason(
                config,
                "NETWORK_BURST",
                "medium",
                f"{ip_count} applications from one network in {config.velocity_window_seconds}s, each from a different device and identity; shared networks such as campuses and offices are common.",
            )
        )

    rotation_start = submitted_at - config.identity_device_window_seconds
    rotation_devices = {
        item.signals.device_id for item in store.recent_by_identity(email, phone, rotation_start, submitted_at)
    }
    rotation_devices.add(application.signals.device_id)
    if len(rotation_devices) >= config.identity_device_rotation_limit:
        found.append(
            reason(
                config,
                "IDENTITY_DEVICE_ROTATION",
                "high",
                f"The same person submitted from {len(rotation_devices)} different devices within {config.identity_device_window_seconds // 60} minutes.",
            )
        )

    is_fast = application.signals.session_seconds < config.min_session_seconds
    if is_fast:
        found.append(
            reason(
                config,
                "FAST_SUBMIT",
                "medium",
                f"Session lasted {application.signals.session_seconds:.1f}s; minimum is {config.min_session_seconds:.1f}s.",
            )
        )
    if is_fast and application.signals.paste_char_ratio >= config.paste_ratio_threshold:
        found.append(
            reason(
                config,
                "PASTE_BULK",
                "medium",
                f"Paste ratio was {application.signals.paste_char_ratio:.2f} during a fast submission.",
            )
        )

    template = template_text(application)
    if template:
        identity_count = store.template_identity_count(template, candidate_identity_key(application))
        if identity_count > config.template_reuse_limit:
            found.append(
                reason(
                    config,
                    "TEMPLATE_REUSE",
                    "medium",
                    f"Identical project text appeared across {identity_count} candidate identities.",
                )
            )
    return found
