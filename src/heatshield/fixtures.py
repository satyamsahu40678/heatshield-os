"""Deterministic synthetic scenarios; every temperature and worker is fabricated."""

from datetime import UTC, datetime, timedelta

from .models import Assignment, Observation, Recovery, Scenario, Schedule, Task, Worker
from .provenance import content_hash


def demo_scenario(seed: int = 7) -> Scenario:
    start = datetime(2026, 7, 15, 2, tzinfo=UTC)  # 06:00 Asia/Dubai
    end = start + timedelta(hours=12)
    skills = ("masonry", "electrical", "logistics")
    workers = tuple(
        Worker(
            id=f"worker-{i + 1:02}",
            skills=(skills[i // 2],),
            available_from=start,
            available_until=end,
            acclimatized=i not in (1, 5),
        )
        for i in range(6)
    )
    tasks = tuple(
        Task(
            id=f"task-{i + 1:02}",
            required_skill=skills[i % 3],
            zone_id="cooled-workshop" if i >= 10 else "east-yard",
            duration_minutes=45 if i in (0, 3, 6) else 30,
            release_at=start,
            deadline=end,
            deferrable=i >= 3,
            outdoors=i < 10,
            heat_exposed=i < 10,
            workload="heavy" if i % 3 == 0 else "moderate",
        )
        for i in range(12)
    )
    curve = (26.0, 26.5, 27.5, 28.0, 29.0, 30.0, 31.0, 32.0, 31.0, 29.0, 27.0, 26.5)
    # Seed changes controlled synthetic heat conditions, never generates a real forecast.
    offset = (seed % 4) * 0.5
    observations = tuple(
        Observation(
            id=f"simulated-wbgt-{hour:02}",
            zone_id="east-yard",
            observed_at=start + timedelta(hours=hour),
            valid_from=start + timedelta(hours=hour),
            valid_until=start + timedelta(hours=hour + 1),
            wbgt_c=temperature + offset,
            source="simulated",
        )
        for hour, temperature in enumerate(curve)
    )
    return Scenario(
        id=f"synthetic-abu-dhabi-{seed}",
        starts_at=start,
        ends_at=end,
        workers=workers,
        tasks=tasks,
        observations=observations,
        cooling_capacity=1 + seed % 2,
        seed=seed,
    )


def fixed_roster(scenario: Scenario) -> Schedule:
    """Deliberately unchecked reference roster; never used as an optimization fallback."""
    assignments = []
    recoveries = []
    next_start = {worker.id: scenario.starts_at + timedelta(hours=6) for worker in scenario.workers}
    for task in scenario.tasks:
        qualified = [w for w in scenario.workers if task.required_skill in w.skills]
        # The unchecked reference remains representable under skill-shortage stress cases.
        # Its independent report records SKILL rather than crashing the benchmark.
        qualified = qualified or list(scenario.workers)
        worker = min(qualified, key=lambda w: (next_start[w.id], w.id))
        start = next_start[worker.id]
        end = start + timedelta(minutes=task.duration_minutes)
        assignments.append(
            Assignment(task_id=task.id, worker_id=worker.id, starts_at=start, ends_at=end)
        )
        if task.heat_exposed:
            recovery_end = end + timedelta(minutes=scenario.policy.recovery_minutes)
            recoveries.append(
                Recovery(
                    task_id=task.id,
                    worker_id=worker.id,
                    starts_at=end,
                    ends_at=recovery_end,
                )
            )
            next_start[worker.id] = recovery_end
        else:
            next_start[worker.id] = end
    return Schedule(
        scenario_hash=content_hash(scenario),
        policy_hash=content_hash(scenario.policy),
        assignments=tuple(assignments),
        recoveries=tuple(recoveries),
    )
