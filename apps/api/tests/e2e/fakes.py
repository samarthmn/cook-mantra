"""Complete deterministic service doubles for the API journey."""

from base64 import b64decode

from core.errors import AppError, ErrorCode
from domain.images import GeneratedImage, ImageGenerationRequest
from domain.ingredients import DetectedIngredient, ExtractionResult
from domain.recipe_options import (
    Difficulty,
    IngredientRequirement,
    NutritionEstimate,
    RecipeOption,
    RecipeOptionDraft,
    RecipePreferences,
)
from domain.recipes import (
    CompleteRecipe,
    IngredientAvailability,
    RecipeIngredient,
    RecipeStep,
)
from services.image_generation import ProgressCallback

PNG_BYTES = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP8zwACTGCS"
    "AQANHQEDgslx/wAAAABJRU5ErkJggg=="
)


class DeterministicIngredientExtractor:
    """Return a realistic visible-ingredient result without a model call."""

    async def extract(self, image: bytes, media_type: str) -> ExtractionResult:
        if image != PNG_BYTES or media_type != "image/png":
            raise AssertionError("The extraction boundary received the wrong image.")
        return ExtractionResult(
            detected=[
                DetectedIngredient(name="Tomato", confidence=0.98),
                DetectedIngredient(name="Onion", confidence=0.94),
                DetectedIngredient(name="Potato", confidence=0.88),
            ],
            warnings=["Review every detected ingredient before continuing."],
        )


def _draft(name: str, *, missing: str, optional: str) -> RecipeOptionDraft:
    return RecipeOptionDraft(
        name=name,
        summary=f"A deterministic {name.lower()} for the complete API journey.",
        cuisine="Indian",
        total_minutes=30,
        difficulty=Difficulty.EASY,
        used_ingredients=["Tomato", "Onion"],
        missing_ingredients=[
            IngredientRequirement(
                name=missing,
                reason="Needed to complete the dish.",
                substitution=f"Use another grain instead of {missing}.",
            )
        ],
        optional_ingredients=[
            IngredientRequirement(
                name=optional,
                reason="Adds a fresh finish.",
                substitution=None,
            )
        ],
    )


class DeterministicMasterChef:
    """Return globally distinct first and More batches."""

    async def generate(
        self,
        ingredients: list[str],
        preferences: RecipePreferences,
        excluded_names: set[str],
    ) -> list[RecipeOptionDraft]:
        if ingredients != ["Tomato", "Onion"]:
            raise AssertionError("Only confirmed ingredients may reach Master Chef.")
        first_batch = [
            _draft("Tomato Onion Curry", missing="Rice", optional="Cilantro"),
            _draft("Tomato Onion Soup", missing="Stock", optional="Cream"),
        ]
        more_batch = [
            _draft("Tomato Rice Bowl", missing="Rice", optional="Sesame"),
            _draft("Onion Tomato Skillet", missing="Bread", optional="Parsley"),
        ]
        batch = more_batch if excluded_names else first_batch
        return batch[: preferences.option_count]


class DeterministicNutritionAgent:
    """Populate every public nutrition field."""

    async def estimate(
        self,
        option: RecipeOptionDraft,
        preferences: RecipePreferences,
    ) -> NutritionEstimate:
        return NutritionEstimate(
            calories_kcal=320,
            protein_g=9.5,
            carbohydrates_g=48.0,
            fat_g=10.0,
            diet_tags=[*preferences.dietary_preferences],
            allergen_warnings=[*preferences.allergens],
        )


class DeterministicImageGenerator:
    """Return one valid PNG while exercising progress reporting."""

    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: ProgressCallback,
    ) -> GeneratedImage:
        if not request.prompt:
            raise AssertionError("Dish previews require a prompt.")
        await progress(35)
        await progress(100)
        return GeneratedImage(
            data=PNG_BYTES,
            media_type="image/png",
            width=2,
            height=2,
        )


class DeterministicSpecializedRecipeAgent:
    """Return one complete recipe and one safe partial failure."""

    async def generate(
        self,
        option: RecipeOption,
        confirmed_ingredients: list[str],
        preferences: RecipePreferences,
    ) -> CompleteRecipe:
        if confirmed_ingredients != ["Tomato", "Onion"]:
            raise AssertionError("Only confirmed ingredients may reach recipe agents.")
        if option.name == "Tomato Onion Soup":
            raise AppError(
                code=ErrorCode.MODEL_OUTPUT_INVALID,
                message="The soup recipe could not be generated.",
                status_code=502,
                retryable=True,
            )

        ingredients = [
            RecipeIngredient(
                name="Tomato",
                quantity="2 medium",
                availability=IngredientAvailability.AVAILABLE,
                substitution=None,
            ),
            RecipeIngredient(
                name="Onion",
                quantity="1 medium",
                availability=IngredientAvailability.AVAILABLE,
                substitution=None,
            ),
            *[
                RecipeIngredient(
                    name=requirement.name,
                    quantity="1 cup",
                    availability=IngredientAvailability.MISSING,
                    substitution=requirement.substitution,
                )
                for requirement in option.missing_ingredients
            ],
            *[
                RecipeIngredient(
                    name=requirement.name,
                    quantity="2 tablespoons",
                    availability=IngredientAvailability.OPTIONAL,
                    substitution=requirement.substitution,
                )
                for requirement in option.optional_ingredients
            ],
        ]
        return CompleteRecipe(
            option_id=option.id,
            name=option.name,
            cuisine=option.cuisine,
            servings=preferences.servings,
            total_minutes=option.total_minutes,
            ingredients=ingredients,
            steps=[
                RecipeStep(
                    number=1,
                    instruction="Prepare and measure every ingredient.",
                    duration_minutes=5,
                ),
                RecipeStep(
                    number=2,
                    instruction="Cook until the vegetables are tender.",
                    duration_minutes=25,
                ),
            ],
            tips=["Taste before serving."],
            substitutions=["Use the listed grain substitution when needed."],
            assumptions=["A clean pan and heat source are available."],
            warnings=["Purchase every ingredient marked missing."],
        )
