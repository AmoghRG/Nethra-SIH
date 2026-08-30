from integration import process_event


def process_events(events):
    results = []

    for event in events:
        result = process_event(event)
        results.append(result)

    return results