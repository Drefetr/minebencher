"""White-noise baseline: the floor every real agent must clear."""
import random

from minebencher.agent import Move, Observation


class RandomAgent:
    """White noise: open a uniformly random covered cell, never flag."""

    agent_id = "random"
    version = "1.0"
    baseline = "floor"

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def act(self, obs: Observation) -> list[Move]:
        cells = obs.covered()
        if not cells:
            return []
        x, y = self.rng.choice(cells)
        return [Move("open", x, y, certain=False)]
