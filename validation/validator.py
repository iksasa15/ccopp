"""
LLM Output Validator — Forces compliant outputs from local models.

Strategy:
  1. Try direct Pydantic parsing
  2. If failed, try safe_parse_json fallback
  3. If still failed, ask the LLM to fix its own output (up to N retries)
  4. If all fails, return a structured error finding (graceful degradation)
"""

from typing import Any, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from validation.schemas import safe_parse_json

T = TypeVar("T", bound=BaseModel)


class ValidationFailure(Exception):
    """Raised when an LLM output cannot be coerced into the expected schema."""

    def __init__(self, original_output: str, errors: list[str], attempts: int):
        self.original_output = original_output
        self.errors = errors
        self.attempts = attempts
        super().__init__(
            f"LLM output failed validation after {attempts} attempts. "
            f"Errors: {'; '.join(errors[:3])}"
        )


class LLMValidator:
    """
    Validates and self-corrects LLM outputs against Pydantic schemas.
    
    Usage:
        validator = LLMValidator(llm_client)
        verdict = await validator.parse(raw_output, LLMVerdict, system_prompt, user_prompt)
    """

    def __init__(self, llm_client: Any, max_correction_attempts: int = 2):
        self.llm = llm_client
        self.max_attempts = max_correction_attempts

    async def parse(
        self,
        raw_output: str,
        schema: type[T],
        original_system_prompt: str = "",
        original_user_prompt: str = "",
    ) -> T:
        """
        Parse and validate. If validation fails, self-correct via LLM.
        
        Raises ValidationFailure if all retries exhausted.
        """
        all_errors: list[str] = []

        # Attempt 0: direct parse
        result = self._try_parse(raw_output, schema)
        if isinstance(result, schema):
            return result
        all_errors.append(str(result))

        # Attempts 1..N: self-correction loop
        current_output = raw_output
        for attempt in range(1, self.max_attempts + 1):
            logger.warning(
                f"Validation failed (attempt {attempt}). "
                f"Asking LLM to self-correct..."
            )

            corrected = await self._request_correction(
                broken_output=current_output,
                error_message=str(result),
                schema=schema,
                original_system_prompt=original_system_prompt,
                original_user_prompt=original_user_prompt,
            )

            if corrected is None:
                all_errors.append(f"Attempt {attempt}: LLM returned no correction")
                continue

            result = self._try_parse(corrected, schema)
            if isinstance(result, schema):
                logger.info(f"Self-correction succeeded on attempt {attempt}")
                return result

            all_errors.append(f"Attempt {attempt}: {result}")
            current_output = corrected

        raise ValidationFailure(
            original_output=raw_output,
            errors=all_errors,
            attempts=self.max_attempts + 1,
        )

    def _try_parse(self, text: str, schema: type[T]) -> T | str:
        """Try parsing once. Return instance on success, error string on failure."""
        # Strategy 1: try Pydantic's native JSON parsing
        try:
            return schema.model_validate_json(text)
        except (ValidationError, ValueError):
            pass

        # Strategy 2: extract JSON robustly, then validate
        parsed = safe_parse_json(text)
        if parsed is None:
            return "No valid JSON object found in output"

        try:
            return schema.model_validate(parsed)
        except ValidationError as e:
            return self._format_validation_errors(e)

    def _format_validation_errors(self, error: ValidationError) -> str:
        """Make Pydantic errors more LLM-friendly."""
        messages = []
        for err in error.errors():
            loc = ".".join(str(x) for x in err["loc"])
            messages.append(f"Field '{loc}': {err['msg']}")
        return " | ".join(messages)

    async def _request_correction(
        self,
        broken_output: str,
        error_message: str,
        schema: type[T],
        original_system_prompt: str,
        original_user_prompt: str,
    ) -> str | None:
        """Ask the LLM to fix its own malformed output."""
        schema_json = schema.model_json_schema()

        correction_prompt = f"""Your previous output failed validation. Fix it.

ORIGINAL TASK:
{original_user_prompt[:500]}

YOUR PREVIOUS OUTPUT (BROKEN):
{broken_output[:1500]}

VALIDATION ERROR:
{error_message}

REQUIRED JSON SCHEMA:
{schema_json}

CRITICAL RULES:
1. Output ONLY valid JSON — no prose, no markdown fences
2. Match the exact schema above
3. All required fields must be present
4. String fields must respect min/max length
5. Enum values must be EXACT (e.g. "high" not "High" or "HIGH")

Output the corrected JSON now:"""

        try:
            response = await self.llm.ainvoke([
                {"role": "system", "content": "You are a JSON formatter. Output only valid JSON."},
                {"role": "user", "content": correction_prompt},
            ])
            return response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            logger.error(f"Correction request failed: {e}")
            return None
