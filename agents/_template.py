"""Starting point for a new agent. Copy, rename, and drop into `agents/`.

Files beginning with an underscore are skipped by discovery, so this one is
never registered or benchmarked.

The contract
------------
`agent_id` and `version` are optional labels for humans. Drop this file in
`agents/` (without a leading underscore) and the bench will load it as a
candidate, always alongside the floor (white noise) and ceiling (oracle).
Do not set `baseline`; that role is reserved for those two controls.

Scores accumulate against a hash of the file, so renaming does not merge
you with anyone else, and editing the file starts a new identity.

Implement `act(obs) -> list[Move]`. Return as many moves as you are confident
in; the harness applies them in order and re-observes after each, skipping
any that have become illegal. Return `[]` to resign, which is scored as
"stuck" rather than as a loss.

Set `certain=False` on any move that is a guess. Nothing enforces this, but
guesses-per-game is one of the more informative metrics while win rates are
still low, so lying to yourself here mostly wastes your own time.

Policies are your business
--------------------------
The harness scores agents, not policies. Hold as many strategies as you like
and switch between them by level, board or position -- `act` is called with
the current observation every time, so nothing stops you dispatching on
`obs.mine_total / (obs.width * obs.height)`, on how open the board is, or on
anything else you can see.

What you cannot do
------------------
`Observation` is the whole interface. There is no mine layout in it and no
route back to the game process, so an agent cannot consult the oracle
regardless of how it is written.
"""
from minebencher.agent import COVERED, FLAGGED, Move, Observation


class TemplateAgent:
    """One-line description; this shows up in the agent listing."""

    agent_id = "template"
    version = "0.1"

    def act(self, obs: Observation) -> list[Move]:
        moves: list[Move] = []

        # Every open cell showing a number is a constraint. This is the
        # entire deduction surface.
        for x, y, count in obs.numbered():
            covered = [(nx, ny) for nx, ny, v in obs.neighbours(x, y)
                       if v == COVERED]
            flags = sum(1 for _, _, v in obs.neighbours(x, y) if v == FLAGGED)

            # Trivial rule: satisfied constraints make every remaining
            # neighbour safe.
            if covered and flags == count:
                moves += [Move("open", nx, ny, certain=True)
                          for nx, ny in covered]

        if moves:
            return moves

        # No certain move: guess, and say so.
        cells = obs.covered()
        return [Move("open", *cells[0], certain=False)] if cells else []
