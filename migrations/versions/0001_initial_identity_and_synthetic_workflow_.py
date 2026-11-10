"""Initial identity and synthetic workflow schema

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Explicit schema snapshot; application model changes require a new revision.
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_events_entity_id"), ["entity_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_audit_events_organization_id"), ["organization_id"], unique=False
        )

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=140), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "operation", "key"),
    )
    with op.batch_alter_table("idempotency_records", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_idempotency_records_organization_id"), ["organization_id"], unique=False
        )

    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("topic", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("outbox", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_outbox_organization_id"), ["organization_id"], unique=False
        )

    op.create_table(
        "private_health_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=15), nullable=False),
        sa.Column("symptoms_json", sa.JSON(), nullable=False),
        sa.Column("urgent", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("private_health_reports", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_private_health_reports_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_private_health_reports_worker_id"), ["worker_id"], unique=False
        )

    op.create_table(
        "scenarios",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("latest_run_id", sa.String(length=36), nullable=True),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("scenarios", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_scenarios_organization_id"), ["organization_id"], unique=False
        )

    op.create_table(
        "worker_holds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.String(length=64), nullable=True),
        sa.Column("release_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "worker_id"),
    )
    with op.batch_alter_table("worker_holds", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_worker_holds_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_worker_holds_worker_id"), ["worker_id"], unique=False)

    op.create_table(
        "optimization_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("scenario_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("solver_status", sa.String(length=20), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("comparison_json", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("optimization_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_optimization_runs_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_optimization_runs_scenario_id"), ["scenario_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_optimization_runs_status"), ["status"], unique=False)

    op.create_table(
        "scenario_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("scenario_json", sa.JSON(), nullable=False),
        sa.Column("scenario_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scenario_id", "revision"),
    )
    with op.batch_alter_table("scenario_revisions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_scenario_revisions_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_scenario_revisions_scenario_id"), ["scenario_id"], unique=False
        )

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=80), nullable=True),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('admin', 'safety_officer', 'supervisor', 'worker')", name="valid_user_role"
        ),
        sa.CheckConstraint("token_version >= 0", name="nonnegative_token_version"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_users_organization_id"), ["organization_id"], unique=False
        )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("scenario_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("schedule_hash", sa.String(length=64), nullable=False),
        sa.Column("validator_version", sa.String(length=30), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["optimization_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_approvals_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_approvals_scenario_id"), ["scenario_id"], unique=False)

    op.create_table(
        "interventions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=80), nullable=False),
        sa.Column("worker_id", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["optimization_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scenario_id", "revision", "run_id", "task_id"),
    )
    with op.batch_alter_table("interventions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_interventions_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_interventions_scenario_id"), ["scenario_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_interventions_worker_id"), ["worker_id"], unique=False)

    op.create_table(
        "intervention_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("intervention_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["intervention_id"],
            ["interventions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("intervention_id", "event_type"),
    )
    with op.batch_alter_table("intervention_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_intervention_events_intervention_id"), ["intervention_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_intervention_events_organization_id"), ["organization_id"], unique=False
        )


def downgrade() -> None:
    # Explicit schema snapshot; application model changes require a new revision.
    with op.batch_alter_table("intervention_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_intervention_events_organization_id"))
        batch_op.drop_index(batch_op.f("ix_intervention_events_intervention_id"))

    op.drop_table("intervention_events")
    with op.batch_alter_table("interventions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_interventions_worker_id"))
        batch_op.drop_index(batch_op.f("ix_interventions_scenario_id"))
        batch_op.drop_index(batch_op.f("ix_interventions_organization_id"))

    op.drop_table("interventions")
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_approvals_scenario_id"))
        batch_op.drop_index(batch_op.f("ix_approvals_organization_id"))

    op.drop_table("approvals")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_organization_id"))

    op.drop_table("users")
    with op.batch_alter_table("scenario_revisions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scenario_revisions_scenario_id"))
        batch_op.drop_index(batch_op.f("ix_scenario_revisions_organization_id"))

    op.drop_table("scenario_revisions")
    with op.batch_alter_table("optimization_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_optimization_runs_status"))
        batch_op.drop_index(batch_op.f("ix_optimization_runs_scenario_id"))
        batch_op.drop_index(batch_op.f("ix_optimization_runs_organization_id"))

    op.drop_table("optimization_runs")
    with op.batch_alter_table("worker_holds", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_worker_holds_worker_id"))
        batch_op.drop_index(batch_op.f("ix_worker_holds_organization_id"))

    op.drop_table("worker_holds")
    with op.batch_alter_table("scenarios", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scenarios_organization_id"))

    op.drop_table("scenarios")
    with op.batch_alter_table("private_health_reports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_private_health_reports_worker_id"))
        batch_op.drop_index(batch_op.f("ix_private_health_reports_organization_id"))

    op.drop_table("private_health_reports")
    with op.batch_alter_table("outbox", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_outbox_organization_id"))

    op.drop_table("outbox")
    op.drop_table("organizations")
    with op.batch_alter_table("idempotency_records", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_idempotency_records_organization_id"))

    op.drop_table("idempotency_records")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_events_organization_id"))
        batch_op.drop_index(batch_op.f("ix_audit_events_entity_id"))

    op.drop_table("audit_events")
