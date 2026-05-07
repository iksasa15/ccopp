"""
Smart Notification System.

Goals:
  - DO NOT spam the user with low-severity alerts
  - Group related alerts into digests
  - Respect quiet hours
  - Provide actionable buttons in notifications
  - Support multiple channels: Windows Toast, system tray, in-app
"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Any, Callable

from loguru import logger

from validation.schemas import CouncilDecision, Finding, ThreatLevel


class NotificationChannel(str, Enum):
    TOAST = "toast"           # Windows Toast Notification
    TRAY = "tray"             # System tray balloon
    IN_APP = "in_app"         # Inside the dashboard UI
    WEBSOCKET = "websocket"   # Real-time push to UI
    SOUND = "sound"           # Audio alert


@dataclass
class NotificationConfig:
    enabled_channels: set[NotificationChannel] = field(
        default_factory=lambda: {NotificationChannel.TOAST, NotificationChannel.IN_APP}
    )
    
    # Severity thresholds
    min_level_for_toast: ThreatLevel = ThreatLevel.MEDIUM
    min_level_for_sound: ThreatLevel = ThreatLevel.HIGH
    min_level_for_immediate: ThreatLevel = ThreatLevel.CRITICAL
    
    # Throttling
    throttle_window_minutes: int = 15
    max_notifications_per_window: int = 3
    
    # Quiet hours (24h format)
    quiet_hours_start: time = time(22, 0)  # 10 PM
    quiet_hours_end: time = time(7, 0)     # 7 AM
    quiet_hours_override_for_critical: bool = True
    
    # Digest mode: batch low-severity alerts
    digest_interval_minutes: int = 60


@dataclass
class NotificationEvent:
    timestamp: datetime
    title: str
    message: str
    threat_level: ThreatLevel
    actions: list[dict[str, str]] = field(default_factory=list)
    finding_id: str | None = None
    delivered_channels: set[NotificationChannel] = field(default_factory=set)


class NotificationManager:
    """Coordinates notifications across channels with smart throttling."""

    def __init__(self, config: NotificationConfig | None = None):
        self.config = config or NotificationConfig()
        self._recent_events: list[NotificationEvent] = []
        self._pending_digest: list[NotificationEvent] = []
        self._channel_handlers: dict[NotificationChannel, Callable] = {}
        self._websocket_clients: set = set()  # WebSocket connections
        self._digest_task: asyncio.Task | None = None

    def register_handler(
        self, channel: NotificationChannel, handler: Callable
    ) -> None:
        """Register a callback for a specific channel."""
        self._channel_handlers[channel] = handler

    async def notify_decision(self, decision: CouncilDecision) -> None:
        """Main entry point — process a council decision into notifications."""
        if decision.overall_threat_level == ThreatLevel.CLEAN:
            return  # Don't notify on clean scans

        # Group findings by severity
        critical = [f for f in decision.primary_findings 
                   if f.threat_level == ThreatLevel.CRITICAL]
        high = [f for f in decision.primary_findings 
               if f.threat_level == ThreatLevel.HIGH]

        # Critical findings always notify, even in quiet hours
        for finding in critical:
            await self._emit_finding_notification(finding, force=True)

        # High findings notify if not throttled / quiet
        for finding in high:
            await self._emit_finding_notification(finding, force=False)

        # Lower-severity: add to digest
        lower = [f for f in decision.primary_findings 
                if f.threat_level in {ThreatLevel.MEDIUM, ThreatLevel.LOW}]
        for finding in lower:
            event = NotificationEvent(
                timestamp=datetime.utcnow(),
                title=f"{finding.threat_level.value.upper()}: {finding.title}",
                message=finding.description[:200],
                threat_level=finding.threat_level,
                finding_id=finding.finding_id,
            )
            self._pending_digest.append(event)

    async def _emit_finding_notification(
        self, finding: Finding, force: bool = False
    ) -> None:
        """Send notification for a single finding."""
        event = NotificationEvent(
            timestamp=datetime.utcnow(),
            title=f"[{finding.threat_level.value.upper()}] {finding.title}",
            message=finding.description[:200],
            threat_level=finding.threat_level,
            actions=self._build_actions(finding),
            finding_id=finding.finding_id,
        )

        # Check quiet hours
        if not force and self._in_quiet_hours():
            if not (
                self.config.quiet_hours_override_for_critical
                and finding.threat_level == ThreatLevel.CRITICAL
            ):
                logger.debug(f"Suppressed notification (quiet hours): {finding.title}")
                self._pending_digest.append(event)
                return

        # Check throttling
        if not force and self._is_throttled():
            logger.debug(f"Throttled notification: {finding.title}")
            self._pending_digest.append(event)
            return

        # Dispatch to channels
        await self._dispatch(event)

    async def _dispatch(self, event: NotificationEvent) -> None:
        """Send event to all enabled channels."""
        for channel in self.config.enabled_channels:
            # Severity gating per channel
            if (
                channel == NotificationChannel.TOAST
                and event.threat_level.numeric < self.config.min_level_for_toast.numeric
            ):
                continue
            if (
                channel == NotificationChannel.SOUND
                and event.threat_level.numeric < self.config.min_level_for_sound.numeric
            ):
                continue

            handler = self._channel_handlers.get(channel)
            if handler:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(event)
                    else:
                        handler(event)
                    event.delivered_channels.add(channel)
                except Exception as e:
                    logger.error(f"Notification handler {channel} failed: {e}")

        self._recent_events.append(event)

    def _build_actions(self, finding: Finding) -> list[dict[str, str]]:
        """Generate action buttons for the notification."""
        actions = [
            {"id": "view", "label": "View details"},
            {"id": "dismiss", "label": "Dismiss"},
        ]
        
        if finding.recommended_action.value == "quarantine":
            actions.insert(0, {"id": "quarantine", "label": "Quarantine now"})
        elif finding.recommended_action.value == "terminate":
            actions.insert(0, {"id": "terminate", "label": "Terminate process"})
        
        actions.append({"id": "false_positive", "label": "Mark as false positive"})
        return actions

    def _in_quiet_hours(self) -> bool:
        """Check if current time is within configured quiet hours."""
        now = datetime.now().time()
        start = self.config.quiet_hours_start
        end = self.config.quiet_hours_end
        
        if start < end:
            return start <= now <= end
        else:  # crosses midnight
            return now >= start or now <= end

    def _is_throttled(self) -> bool:
        """Are we sending too many notifications recently?"""
        cutoff = datetime.utcnow() - timedelta(minutes=self.config.throttle_window_minutes)
        recent = [
            e for e in self._recent_events
            if e.timestamp > cutoff and e.delivered_channels
        ]
        return len(recent) >= self.config.max_notifications_per_window

    async def start_digest_loop(self) -> None:
        """Background task that emits digest summaries."""
        while True:
            await asyncio.sleep(self.config.digest_interval_minutes * 60)
            await self._emit_digest()

    async def _emit_digest(self) -> None:
        """Combine pending events into a single digest notification."""
        if not self._pending_digest:
            return

        # Group by severity
        by_level: dict[ThreatLevel, list[NotificationEvent]] = defaultdict(list)
        for event in self._pending_digest:
            by_level[event.threat_level].append(event)

        summary_parts = []
        for level in [ThreatLevel.HIGH, ThreatLevel.MEDIUM, ThreatLevel.LOW]:
            if events := by_level.get(level):
                summary_parts.append(f"{len(events)} {level.value}")

        if not summary_parts:
            self._pending_digest.clear()
            return

        digest = NotificationEvent(
            timestamp=datetime.utcnow(),
            title=f"Security Digest: {', '.join(summary_parts)}",
            message=(
                f"While you were away, the council detected "
                f"{len(self._pending_digest)} alert(s). Open the dashboard for details."
            ),
            threat_level=max(by_level.keys(), key=lambda t: t.numeric),
            actions=[{"id": "view_all", "label": "View all"}],
        )

        await self._dispatch(digest)
        self._pending_digest.clear()


# ============================================================
# Channel Handlers
# ============================================================

async def windows_toast_handler(event: NotificationEvent) -> None:
    """Send Windows 10/11 Toast Notification."""
    try:
        # Use winrt for proper toast notifications
        # Fallback to win10toast for simpler integration
        from win10toast import ToastNotifier  # type: ignore
        
        toaster = ToastNotifier()
        # Note: win10toast is synchronous; for production use winrt async API
        await asyncio.to_thread(
            toaster.show_toast,
            event.title,
            event.message,
            duration=10,
            threaded=True,
        )
    except ImportError:
        logger.warning("win10toast not installed; toast notifications disabled")
    except Exception as e:
        logger.error(f"Toast notification failed: {e}")


def in_app_handler_factory(websocket_set: set):
    """Returns a handler that broadcasts to all connected WebSocket clients."""
    async def handler(event: NotificationEvent):
        import json
        payload = json.dumps({
            "type": "notification",
            "timestamp": event.timestamp.isoformat(),
            "title": event.title,
            "message": event.message,
            "level": event.threat_level.value,
            "actions": event.actions,
            "finding_id": event.finding_id,
        })
        dead_clients = set()
        for ws in websocket_set:
            try:
                await ws.send_text(payload)
            except Exception:
                dead_clients.add(ws)
        websocket_set -= dead_clients
    return handler
