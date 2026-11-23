"""Owner-selected Option 2 Ranked point policy, without client save dependencies."""

MAX_RANK = 35
MAX_POINTS = 2147483647
WIN_OVERRIDES = {
    (6, 6, 3, 3): 14,
    (6, 1, 1, 1): 14,
    (7, 2, 1, 1): 16,
    (8, 1, 1, 1): 16,
}


def rank_id(value):
    if type(value) is not int or not 1 <= value <= MAX_RANK:
        raise ValueError('Invalid rank')
    return value


def up_threshold(rank):
    rank_id(rank)
    if rank <= 5: return 10 * rank
    if rank <= 7: return 50 * rank - 200
    if rank <= 13: return 100 * rank - 550
    if rank <= 19: return 200 * rank - 1850
    if rank <= 25: return 300 * rank - 3750
    if rank < MAX_RANK: return 400 * rank - 6250
    return None


def down_threshold(rank):
    rank_id(rank)
    return up_threshold(rank - 1) - 50 if rank >= 10 else None


def points(rank, partner, enemies, won):
    rank_id(rank)
    if type(won) is not bool:
        raise ValueError('Ranked result required')
    if rank <= 5:
        return 10 if won else 5
    if not won and rank <= 9:
        return 0
    rank_id(partner)
    if not isinstance(enemies, (tuple, list)) or len(enemies) != 2:
        raise ValueError('Two opposing ranks required')
    enemies = sorted(rank_id(value) for value in enemies)
    twice_difference = sum(enemies) - rank - partner
    if won:
        return WIN_OVERRIDES.get((rank, partner, *enemies),
                                 max(1, min(32, (25 + 3 * twice_difference) // 2)))
    return -1 if twice_difference > 0 else -max(1, min(32, 12 - 3 * twice_difference))


def validate_progress(rank, total):
    rank_id(rank)
    if type(total) is not int or not 0 <= total <= MAX_POINTS:
        raise ValueError('Invalid rank points')
    lower = (down_threshold(rank) if rank >= 10 else up_threshold(rank - 1)) if rank > 1 else 0
    upper = up_threshold(rank)
    if total < lower or upper is not None and total >= upper:
        raise ValueError('Rank and points disagree')


def advance(rank, total, delta):
    validate_progress(rank, total)
    if type(delta) is not int or not -32 <= delta <= 32:
        raise ValueError('Invalid rank change')
    total = max(0, min(MAX_POINTS, total + delta))
    while rank < MAX_RANK and total >= up_threshold(rank):
        rank += 1
    while rank >= 10 and total < down_threshold(rank):
        rank -= 1
    return rank, total
