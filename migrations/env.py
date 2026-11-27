"""Point d'entrée Alembic : toute la logique est dans le package (`indusense.layers.migrate`).

Utilisable par `uv run indusense db ...` (recommandé) ou directement par `uv run alembic ...`
(config par défaut, ou `-x config=chemin/pipeline.yaml`).
"""
from alembic import context

from indusense.layers.migrate import run_env

run_env(context)
