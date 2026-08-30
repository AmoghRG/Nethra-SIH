def write_incident(event):
    details = []

    event_id = event.get("event_id")
    time = event.get("time")
    event_type = event.get("type")

    if event_id and time and event_type:
        details.append(
            f"Event {event_id} occurred at {time}: {event_type}."
        )

    severity = event.get("severity")
    if severity:
        details.append(f"The conflict was classified as {severity}.")

    vehicle_a = event.get("vehicle_a", {})
    vehicle_b = event.get("vehicle_b", {})

    if vehicle_a.get("type") and vehicle_a.get("speed_kmh") is not None:
        details.append(
            f"A {vehicle_a['type']} was travelling at "
            f"{vehicle_a['speed_kmh']} km/h."
        )

    if vehicle_b.get("type") and vehicle_b.get("speed_kmh") is not None:
        details.append(
            f"A {vehicle_b['type']} was travelling at "
            f"{vehicle_b['speed_kmh']} km/h."
        )

    ttc = event.get("ttc_s")
    pet = event.get("pet_s")

    if ttc is not None and pet is not None:
        details.append(
            f"TTC was {ttc} seconds and PET was {pet} seconds."
        )
    elif ttc is not None:
        details.append(f"TTC was {ttc} seconds.")
    elif pet is not None:
        details.append(f"PET was {pet} seconds.")

    conditions = event.get("conditions", {})
    condition_parts = []

    for key in ["light", "weather", "surface"]:
        value = conditions.get(key)
        if value:
            condition_parts.append(value)

    if condition_parts:
        details.append(
            "Conditions: " + ", ".join(condition_parts) + "."
        )

    return " ".join(details)