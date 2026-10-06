"""Observation store abstraction wrapping Database persistence."""

from typing import List, Optional
from backend.project_model.db import Database
from backend.observation.models import ObservationEvent


class ObservationStore:
    """Manages transactional persistence and retrieval of observation events."""

    def __init__(self, db: Database):
        self.db = db

    def save_event(self, event: ObservationEvent) -> None:
        """Persists a validated and redacted observation event."""
        self.db.insert_observation_event(event)

    def get_timeline(
        self,
        project_id: str,
        limit: int = 1000,
        event_types: Optional[List[str]] = None,
    ) -> List[ObservationEvent]:
        """Retrieves events for a project ordered chronologically (timestamp ASC)."""
        return self.db.list_observation_events(project_id=project_id, limit=limit, event_types=event_types)

    def get_event(self, event_id: str) -> Optional[ObservationEvent]:
        """Loads a single event by ID."""
        return self.db.get_observation_event(event_id)
