"""Perfect play, for calibration only.

This is the one agent that receives the mine layout. Note what that costs
it: to be handed the oracle it must set `privileged = True`, and every run
by a privileged agent is stamped as such at run time and excluded from the
rankings. Cheating is possible, but only by publicly declaring it.
"""
from minebencher.agent import COVERED, Move, Observation


class OracleAgent:
    """Perfect play: the ceiling every real agent is measured against."""

    agent_id = "oracle"
    version = "1.0"
    privileged = True
    baseline = "ceiling"

    def act(self, obs: Observation, mines: list[list[bool]]) -> list[Move]:
        for x, y, v in obs.cells():
            if v == COVERED and not mines[y][x]:
                return [Move("open", x, y, certain=True)]
        return []
