"""initial

Revision ID: 8118058f226a
Revises: 
Create Date: 2026-05-18 21:49:18.400272

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8118058f226a'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # initial state: db without timestamp
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
