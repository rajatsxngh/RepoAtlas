"""Model prices from settings, and the cost of a call (decision 0010: prices live in
configuration).

Prices are US dollars per million tokens: uncached input, output, cache reads, and cache writes
(5-minute TTL). ``REPOATLAS_LLM_PRICES`` overrides the table as JSON, for example
``{"claude-sonnet-5-5": {"input": 2, "output": 10, "cache_read": 0.1, "cache_write": 2.5}}``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from repoatlas.llm.types import Usage

PER_MILLION = 1_000_000


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True)

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float = Field(ge=0)
    cache_write: float = Field(ge=0)


DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-sonnet-5-5": ModelPrice(input=2.00, output=10.00, cache_read=0.10, cache_write=2.50),
    # Prompts up to 100K tokens; longer prompts cost 5x (answers stay far below that).
    "claude-haiku-5-5": ModelPrice(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
}


class UnknownModelPrice(KeyError):
    """No price is configured for the model."""


class PriceTable:
    def __init__(self, prices: dict[str, ModelPrice]) -> None:
        self.prices = dict(prices)

    def price(self, model: str) -> ModelPrice:
        try:
            return self.prices[model]
        except KeyError:
            raise UnknownModelPrice(
                f"No price for {model}; add it to REPOATLAS_LLM_PRICES."
            ) from None

    def cost(self, usage: Usage, model: str) -> float:
        """Dollars for ``usage`` on ``model``."""
        price = self.price(model)
        total = (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_read_tokens * price.cache_read
            + usage.cache_write_tokens * price.cache_write
        )
        return total / PER_MILLION

    def max_cost(self, model: str, input_tokens: int, max_output_tokens: int) -> float:
        """The most a call can cost: every input token uncached, every output token used."""
        price = self.price(model)
        return (input_tokens * price.input + max_output_tokens * price.output) / PER_MILLION


def cost(usage: Usage, model: str, prices: dict[str, ModelPrice] | None = None) -> float:
    """Dollars for ``usage`` on ``model`` with ``prices`` (default: the built-in table)."""
    return PriceTable(prices or DEFAULT_PRICES).cost(usage, model)
