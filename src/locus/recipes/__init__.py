"""Recipe system — composable primitive configurations."""

from locus.recipes.composer import (
    recipe_to_eval_suite,
    recipe_to_operator,
)
from locus.recipes.loader import (
    Recipe,
    discover_recipes,
    load_recipe,
    resolve_recipe,
)

__all__ = [
    "Recipe",
    "discover_recipes",
    "load_recipe",
    "recipe_to_eval_suite",
    "recipe_to_operator",
    "resolve_recipe",
]
