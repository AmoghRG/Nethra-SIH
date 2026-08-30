# NETRA Ground-Truth Labelling Protocol & Borderline Case Policy

> **SIH 2026 Grand Finale — Evaluation Standard (Module M9)**  
> **Author**: Person C (*Norms & Evidence Lead*)  
> **Status**: Frozen Standard Protocol for Blind Ground-Truth Verification

---

## 1. Core Principle: Blind Labelling

To prevent confirmation bias, **the human annotator must label footage blindly before viewing system outputs**. 
The goal is to record real-world road conflicts based strictly on physical vehicle kinematics and human observation.

---

## 2. Label Schema (`eval/groundtruth/labels.csv`)

Every labeled conflict must be recorded as a row in `labels.csv`:

```csv
t_start_s,t_end_s,severity,vehicle_a,vehicle_b,notes
```

### Field Definitions:
- **`t_start_s`** *(float)*: Timestamp (in seconds from video start) when the conflict trajectory began (e.g. when vehicle A begins turning across vehicle B's path).
- **`t_end_s`** *(float)*: Timestamp (in seconds) when the conflict point was cleared or vehicles resumed normal trajectory.
- **`severity`** *(enum)*: Strictly `conflict` or `severe`.
- **`vehicle_a`** *(enum)*: `car`, `motorcycle`, `truck`, `bus`, `auto`, `pedestrian`.
- **`vehicle_b`** *(enum)*: `car`, `motorcycle`, `truck`, `bus`, `auto`, `pedestrian`.
- **`notes`** *(string)*: Concise description of geometry and maneuver (e.g., `blind crossing near-miss, swerved left`).

---

## 3. Severity Criteria & Classification Rules

| Severity Level | Physical & Visual Definition | Surrogate Indicator Benchmark |
|---|---|---|
| **`severe`** | High-danger event. Urgent evasive action required: aggressive swerving, tyre screech / hard lockup braking, or near-collision clearance ($< 1.0$ m). | $\text{TTC} < 0.8\text{ s}$ or $\text{PET} < 1.0\text{ s}$ |
| **`conflict`** | Moderate danger event. Noticeable deceleration or course alteration to avoid collision, but with controlled avoidance margin ($1.0 - 2.5$ m). | $0.8\text{ s} \le \text{TTC} < 1.5\text{ s}$ or $1.0\text{ s} \le \text{PET} < 2.0\text{ s}$ |
| **`non-conflict` (Ignore)** | Normal flow, smooth queue deceleration, lane changes with ample gap ($> 3.0$ m / $> 2.0$ s buffer). | $\text{TTC} \ge 1.5\text{ s}$ |

---

## 4. Borderline Case Policy

When an encounter is ambiguous, apply these standard tie-breakers:

1. **Slow-speed bumper-to-bumper crawling ($< 10$ km/h)**:
   - *Rule*: **DO NOT LABEL** as a conflict unless an aggressive sudden cut-in causes emergency braking. Indian junction crawl is expected baseline flow, not near-miss danger.
2. **Pedestrian crossing with pedestrian hesitation**:
   - *Rule*: If the vehicle does not alter speed and the pedestrian pauses with $> 2.0$ m margin, classify as **non-conflict**. If the vehicle brakes abruptly or pedestrian jumps back, classify as **`conflict`** (or **`severe`** if vehicle speed $> 30$ km/h).
3. **Wrong-Way Driving (`against flow`)**:
   - *Rule*: If a wrong-way vehicle encounters an oncoming vehicle in the same lane requiring a swerve, classify as **`severe`** if closing speed is high ($> 40$ km/h combined), otherwise **`conflict`**.
4. **Tailgating / Close Following**:
   - *Rule*: Only classify as a rear-end conflict if the leading vehicle decelerates and following vehicle executes hard braking or rapid avoidance. Stable close following without speed divergence is non-conflict.

---

## 5. Labelling Target

- **Minimum Required Video Duration**: $\ge 30$ minutes of continuous footage (or $\ge 15$ minutes under time pressure).
- **Target Conflict Count**: Typically 10–30 genuine conflicts per 30-min Indian urban junction clip.
