"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Script GÉNÉRÉ par `indusense db revision` à partir des contrats et des fichiers réels.
À RELIRE avant `indusense db upgrade`, en particulier toute suppression (table, colonne,
contrainte) ou tout changement de type : une suppression au Bronze perd des lots chargés.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# Identifiants de révision utilisés par Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Amène la base à cette révision."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Revient à la révision précédente."""
    ${downgrades if downgrades else "pass"}
