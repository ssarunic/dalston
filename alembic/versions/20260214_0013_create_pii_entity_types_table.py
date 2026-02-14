"""Create pii_entity_types reference table.

Revision ID: 0013
Revises: 0012
Create Date: 2026-02-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Default entity types to seed
DEFAULT_ENTITY_TYPES = [
    # PII Category (Personal)
    ("name", "pii", "Name", "Person name", "gliner", True),
    ("name_given", "pii", "Given Name", "First/given name", "gliner", False),
    ("name_family", "pii", "Family Name", "Last/family name", "gliner", False),
    ("email_address", "pii", "Email Address", "Email address", "regex", True),
    ("phone_number", "pii", "Phone Number", "Phone number (international formats)", "regex", True),
    ("ssn", "pii", "SSN", "US Social Security Number", "regex", True),
    ("location", "pii", "Location", "Geographic location", "gliner", True),
    ("location_address", "pii", "Address", "Street address", "gliner", False),
    ("date_of_birth", "pii", "Date of Birth", "Birth date", "gliner", True),
    ("age", "pii", "Age", "Age mention", "gliner", False),
    ("ip_address", "pii", "IP Address", "IPv4 or IPv6 address", "regex", True),
    ("driver_license", "pii", "Driver License", "Driver license number", "regex", False),
    ("passport_number", "pii", "Passport Number", "Passport number", "regex", False),
    ("organization", "pii", "Organization", "Company or organization name", "gliner", False),
    # PCI Category (Payment Card Industry)
    ("credit_card_number", "pci", "Credit Card Number", "Credit card number with Luhn validation", "regex+luhn", True),
    ("credit_card_cvv", "pci", "CVV", "Card verification value", "regex+context", True),
    ("credit_card_expiry", "pci", "Card Expiry", "Credit card expiration date", "regex", True),
    ("iban", "pci", "IBAN", "International Bank Account Number with mod-97 validation", "regex+checksum", True),
    ("bank_account", "pci", "Bank Account", "Bank account number", "gliner", False),
    # PHI Category (Protected Health Information)
    ("medical_record_number", "phi", "Medical Record Number", "Medical record/chart number", "regex", False),
    ("medical_condition", "phi", "Medical Condition", "Health condition or diagnosis", "gliner", False),
    ("medication", "phi", "Medication", "Medication or drug name", "gliner", False),
    ("health_plan_id", "phi", "Health Plan ID", "Health insurance plan identifier", "regex", False),
    # Regional (SE European)
    ("jmbg", "pii", "JMBG", "Serbian/Yugoslav national ID (mod-11 checksum)", "regex+checksum", False),
    ("oib", "pii", "OIB", "Croatian personal identification number (mod-11 checksum)", "regex+checksum", False),
]


def upgrade() -> None:
    # Create pii_entity_types reference table
    op.create_table(
        "pii_entity_types",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("detection_method", sa.String(50), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # Create index on category for filtering
    op.create_index(
        "ix_pii_entity_types_category",
        "pii_entity_types",
        ["category"],
    )

    # Seed with default entity types
    pii_entity_types = sa.table(
        "pii_entity_types",
        sa.column("id", sa.String),
        sa.column("category", sa.String),
        sa.column("display_name", sa.String),
        sa.column("description", sa.Text),
        sa.column("detection_method", sa.String),
        sa.column("is_default", sa.Boolean),
    )

    op.bulk_insert(
        pii_entity_types,
        [
            {
                "id": entity_id,
                "category": category,
                "display_name": display_name,
                "description": description,
                "detection_method": method,
                "is_default": is_default,
            }
            for entity_id, category, display_name, description, method, is_default in DEFAULT_ENTITY_TYPES
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_pii_entity_types_category", table_name="pii_entity_types")
    op.drop_table("pii_entity_types")
