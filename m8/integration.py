try:
    from .incident_writer import write_incident
except ImportError:
    from incident_writer import write_incident


def process_event(event):
    narration = write_incident(event)

    return {
        "event_id": event["event_id"],
        "narration": narration
    }