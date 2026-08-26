from __future__ import annotations

from dataclasses import dataclass
from math import floor



START_ROUND = 1

MAP_DIFFICULTY = "Advanced"


ENERGIZER_ROUND: int | None = None


MONKEY_EDUCATION = True
SCHOLARSHIPS = True
SELF_TAUGHT_HEROES = True
EMPOWERED_HEROES = True
MONKEYS_TOGETHER_STRONG = True


HEROES = [
    {"hero": "Ezili",   "goallevel": 17, "endround": 120, "replaced": False},
    {"hero": "Ezili",   "goallevel": 7,  "endround": 119, "replaced": False},
    {"hero": "Geraldo", "goallevel": 20, "endround": 122, "replaced": True},
    {"hero": "Etienne", "goallevel": 16, "endround": 120, "replaced": False},
]


BASE_LEVEL_XP = (
    180,
    460,
    1000,
    1860,
    3280,
    5180,
    8320,
    9380,
    13620,
    16380,
    14400,
    16650,
    14940,
    16380,
    17820,
    19260,
    20700,
    16470,
    17280,
)

HERO_RATIOS = {
    # x1.0
    "Quincy": 1.0,
    "Gwendolin": 1.0,
    "Striker Jones": 1.0,
    "Obyn Greenfoot": 1.0,
    "Etienne": 1.0,
    "Geraldo": 1.0,

    # x1.425
    "Ezili": 1.425,
    "Pat Fusty": 1.425,
    "Admiral Brickell": 1.425,
    "Sauda": 1.425,
    "Corvus": 1.425,
    "Rosalia": 1.425,
    "Dan D'Monke": 1.425,

    # x1.5
    "Benjamin": 1.5,
    "Psi": 1.5,
    "Silas": 1.5,

    # x1.71
    "Captain Churchill": 1.71,
    "Adora": 1.71,
}

MAP_XP_MULTIPLIER = {
    "Beginner": 1.00,
    "Intermediate": 1.10,
    "Advanced": 1.20,
    "Expert": 1.30,
}


@dataclass(frozen=True)
class HeroGoal:
    index: int
    hero: str
    goal_level: int
    end_round: int
    replaced: bool


@dataclass
class HeroState:
    goal: HeroGoal
    action_round: int
    label: str


def round_half_up(value: float) -> int:
    return floor(value + 0.5)


def hero_thresholds(hero: str) -> list[int]:
    """
    Cumulative XP required for levels 1..20.

    index 0  -> Level 1
    index 1  -> Level 2
    ...
    index 19 -> Level 20
    """
    ratio = HERO_RATIOS[hero]

    thresholds = [0]
    total = 0

    for base_xp in BASE_LEVEL_XP:
        total += round_half_up(base_xp * ratio)
        thresholds.append(total)

    return thresholds


def base_round_xp(round_number: int) -> int:

    if round_number <= 20:
        return 20 * round_number + 20

    if round_number <= 50:
        return 40 * round_number - 380

    return 90 * round_number - 2880


def monkey_knowledge_multiplier(active_heroes: int) -> float:

    multiplier = 1.0

    if MONKEY_EDUCATION:
        multiplier *= 1.08

    if SELF_TAUGHT_HEROES:
        multiplier *= 1.10

    if MONKEYS_TOGETHER_STRONG:
        multiplier *= 1.0 + (0.05 * active_heroes)

    return multiplier


def energizer_multiplier(round_number: int) -> float:

    if ENERGIZER_ROUND is None:
        return 1.0

    if round_number >= ENERGIZER_ROUND:
        return 1.50

    return 1.0


def shared_round_xp(round_number: int, active_heroes: int) -> float:

    if active_heroes <= 0:
        return 0.0

    return (
        base_round_xp(round_number)
        * MAP_XP_MULTIPLIER[MAP_DIFFICULTY]
        * monkey_knowledge_multiplier(active_heroes)
        * energizer_multiplier(round_number)
        / active_heroes
    )


def starting_xp(goal: HeroGoal) -> float:
    thresholds = hero_thresholds(goal.hero)

    if goal.replaced:
        starting_level = 1
    elif EMPOWERED_HEROES:
        starting_level = 3
    else:
        starting_level = 1

    return float(thresholds[starting_level - 1])


def starting_level(goal: HeroGoal) -> int:
    if goal.replaced:
        return 1

    return 3 if EMPOWERED_HEROES else 1


def level_from_xp(hero: str, xp: float) -> int:
    thresholds = hero_thresholds(hero)

    level = 1

    for candidate in range(2, 21):
        if xp >= thresholds[candidate - 1]:
            level = candidate
        else:
            break

    return level


def hero_is_active(state: HeroState, round_number: int) -> bool:

    if state.goal.replaced:
        return round_number >= START_ROUND

    return round_number >= state.action_round


def xp_after_action(
    target: HeroState,
    states: list[HeroState],
    action_round: int | None = None,
) -> float:

    start = target.action_round if action_round is None else action_round

    xp = starting_xp(target.goal)

    for round_number in range(start, target.goal.end_round):
        active_count = 0

        for state in states:
            if state is target:
                if target.goal.replaced:
                    is_active = (
                        round_number >= START_ROUND
                    )
                else:
                    is_active = round_number >= start
            else:
                is_active = hero_is_active(state, round_number)

            if is_active:
                active_count += 1

        xp += shared_round_xp(
            round_number=round_number,
            active_heroes=active_count,
        )

    return xp


def achieved_level(
    target: HeroState,
    states: list[HeroState],
    action_round: int | None = None,
) -> int:
    return level_from_xp(
        target.goal.hero,
        xp_after_action(target, states, action_round),
    )


def goal_satisfied(
    target: HeroState,
    states: list[HeroState],
    action_round: int | None = None,
) -> bool:
    return (
        achieved_level(target, states, action_round)
        >= target.goal.goal_level
    )


def latest_valid_round(
    target: HeroState,
    states: list[HeroState],
) -> int | None:

    earliest = START_ROUND

    if (
        not target.goal.replaced
        and starting_level(target.goal) >= target.goal.goal_level
    ):

        return target.goal.end_round

    latest_action_round = target.goal.end_round - 1

    for candidate in range(
        latest_action_round,
        earliest - 1,
        -1,
    ):
        if goal_satisfied(target, states, candidate):
            return candidate

    return None


def optimize_schedule(states: list[HeroState]) -> None:

    changed = True

    while changed:
        changed = False

        placements = sorted(
            (
                state
                for state in states
                if not state.goal.replaced
            ),
            key=lambda state: (
                state.goal.end_round,
                -state.goal.goal_level,
                state.goal.index,
            ),
        )

        for state in placements:
            latest = latest_valid_round(state, states)

            if (
                latest is not None
                and latest > state.action_round
            ):
                state.action_round = latest
                changed = True

    for state in states:
        if state.goal.replaced:
            latest = latest_valid_round(state, states)

            if latest is not None:
                state.action_round = latest


def normalize_name(name: str) -> str:
    return " ".join(name.strip().split()).casefold()


def resolve_hero_name(name: str) -> str:
    aliases = {
        normalize_name(hero): hero
        for hero in HERO_RATIOS
    }

    aliases.update({
        "gwen": "Gwendolin",
        "striker": "Striker Jones",
        "obyn": "Obyn Greenfoot",
        "brickell": "Admiral Brickell",
        "churchill": "Captain Churchill",
        "dan": "Dan D'Monke",
    })

    key = normalize_name(name)

    if key not in aliases:
        valid = ", ".join(HERO_RATIOS)

        raise ValueError(
            f"Unknown hero {name!r}.\n"
            f"Valid heroes: {valid}"
        )

    return aliases[key]


def load_goals() -> list[HeroGoal]:
    if not isinstance(START_ROUND, int) or START_ROUND < 1:
        raise ValueError("START_ROUND must be a positive integer.")

    if MAP_DIFFICULTY not in MAP_XP_MULTIPLIER:
        raise ValueError(
            "MAP_DIFFICULTY must be one of: "
            + ", ".join(MAP_XP_MULTIPLIER)
        )

    if ENERGIZER_ROUND is not None:
        if not isinstance(ENERGIZER_ROUND, int):
            raise ValueError(
                "ENERGIZER_ROUND must be an integer or None."
            )

        if ENERGIZER_ROUND < START_ROUND:
            raise ValueError(
                f"ENERGIZER_ROUND cannot be before START_ROUND "
                f"(R{START_ROUND})."
            )

    if not HEROES:
        raise ValueError("HEROES cannot be empty.")

    if len(HEROES) > 4:
        raise ValueError(
            "Normal BTD6 co-op supports at most 4 player Heroes."
        )

    goals: list[HeroGoal] = []

    for index, raw in enumerate(HEROES, 1):
        try:
            hero = resolve_hero_name(str(raw["hero"]))
            goal_level = int(raw["goallevel"])
            end_round = int(raw["endround"])
            replaced = bool(raw["replaced"])
        except KeyError as exc:
            raise ValueError(
                f"HEROES entry #{index} is missing "
                f"{exc.args[0]!r}."
            ) from exc

        if not 1 <= goal_level <= 20:
            raise ValueError(
                f"{hero}: goallevel must be from 1 to 20."
            )

        if end_round < START_ROUND:
            raise ValueError(
                f"{hero}: endround must be at least "
                f"START_ROUND (R{START_ROUND})."
            )

        goals.append(
            HeroGoal(
                index=index,
                hero=hero,
                goal_level=goal_level,
                end_round=end_round,
                replaced=replaced,
            )
        )

    return goals


def make_labels(goals: list[HeroGoal]) -> dict[int, str]:
    totals: dict[str, int] = {}

    for goal in goals:
        totals[goal.hero] = totals.get(goal.hero, 0) + 1

    seen: dict[str, int] = {}
    labels: dict[int, str] = {}

    for goal in goals:
        seen[goal.hero] = seen.get(goal.hero, 0) + 1

        if totals[goal.hero] == 1:
            labels[goal.index] = goal.hero
        else:
            labels[goal.index] = (
                f"{goal.hero} #{seen[goal.hero]}"
            )

    return labels


def enabled_text(value: bool) -> str:
    return "ON" if value else "OFF"


def print_schedule(states: list[HeroState]) -> None:
    print()
    print("=" * 90)
    print("BTD6 CO-OP HERO SCHEDULE")
    print("=" * 90)
    print(f"Start round:              R{START_ROUND}")
    print(f"Map difficulty:           {MAP_DIFFICULTY}")
    print("Deadline semantics:       goal level required at START of endround")
    print("Placement semantics:      Rn = place/replace during Rn before it ends")

    if ENERGIZER_ROUND is None:
        print("Energizer:                OFF")
    else:
        print(
            f"Energizer:                R{ENERGIZER_ROUND}+ "
            f"(x1.50 Hero XP)"
        )

    print()
    print("Monkey Knowledge:")
    print(
        f"  Monkey Education:       "
        f"{enabled_text(MONKEY_EDUCATION)}"
    )
    print(
        f"  Scholarships:           "
        f"{enabled_text(SCHOLARSHIPS)} "
        f"(upgrade cost only)"
    )
    print(
        f"  Self Taught Heroes:     "
        f"{enabled_text(SELF_TAUGHT_HEROES)}"
    )
    print(
        f"  Empowered Heroes:       "
        f"{enabled_text(EMPOWERED_HEROES)}"
    )
    print(
        f"  Monkeys Together Strong:"
        f" {enabled_text(MONKEYS_TOGETHER_STRONG)}"
    )
    print("=" * 90)

    impossible: list[HeroState] = []

    for state in states:
        if not goal_satisfied(state, states):
            impossible.append(state)

    scheduled = sorted(
        (
            state
            for state in states
            if state not in impossible
        ),
        key=lambda state: (
            state.action_round,
            state.goal.end_round,
            state.goal.index,
        ),
    )

    print("\nRECOMMENDED ACTIONS")
    print("-" * 90)

    if not scheduled:
        print("No requested Hero goal is currently achievable.")
    else:
        for state in scheduled:
            action = (
                "REPLACE"
                if state.goal.replaced
                else "PLACE"
            )

            actual = achieved_level(state, states)

            immediate_at_deadline = (
                not state.goal.replaced
                and state.action_round == state.goal.end_round
                and starting_level(state.goal) >= state.goal.goal_level
            )

            round_label = (
                f"START R{state.action_round}"
                if immediate_at_deadline
                else f"R{state.action_round}"
            )

            print(
                f"{round_label:<14} "
                f"{action:<10} "
                f"{state.label:<22} "
                f"-> L{actual} at START R{state.goal.end_round} "
                f"(goal L{state.goal.goal_level})"
            )

    if impossible:
        print("\nIMPOSSIBLE GOALS")
        print("-" * 90)

        earliest = START_ROUND

        for state in impossible:
            level_at_earliest = achieved_level(
                state,
                states,
                earliest,
            )

            print(
                f"{state.label}: needs L{state.goal.goal_level} "
                f"at START R{state.goal.end_round}; even acting during "
                f"R{earliest} only reaches L{level_at_earliest}."
            )



def main() -> None:
    goals = load_goals()
    labels = make_labels(goals)
    earliest = START_ROUND

    states = [
        HeroState(
            goal=goal,
            action_round=earliest,
            label=labels[goal.index],
        )
        for goal in goals
    ]

    optimize_schedule(states)
    print_schedule(states)


if __name__ == "__main__":
    main()
