"""Bang!: The Dice Game with Geneity house rules."""
# TODO: Migrate to Python 3.5 and async/await.
import collections
import contextlib
import random

Face = collections.namedtuple('Face', 'name')
arrow = Face("arrow")
beer = Face("beer")
dynamite = Face("dynamite")
gatling = Face("gatling")
shoot_1 = Face("1")
shoot_2 = Face("2")

ChooseAction = collections.namedtuple('ChooseAction', 'actions')
ChooseDice = collections.namedtuple('ChooseDice', 'dice')
ChoosePlayer = collections.namedtuple('ChoosePlayer', 'players')

GameStarted = collections.namedtuple('GameStarted', 'players')
TurnStarted = collections.namedtuple('TurnStarted', 'player')
DiceRolled = collections.namedtuple('DiceRolled', 'dice')
Dynamites = collections.namedtuple('Dynamites', 'player')
IndianAttack = collections.namedtuple('IndianAttack', [])
Died = collections.namedtuple('Died', 'player')

class EndTurn(Exception): pass

class Pool:
    def __init__(self, faces):
        self.dice = collections.Counter(faces)

    def __len__(self):
        return sum(self.dice.values())

    def add(self, other):
        self.dice.update(other.dice)

    def remove(self, other):
        self.dice = +(self.dice - other.dice)

    def filter(self, predicate):
        return Pool({k: v for k, v in self.dice.items() if predicate(k)})

    def __getitem__(self, key):
        return self.dice[key]

    def __le__(self, other):
        return all(v <= other[k] for k, v in self.dice.items())

Config = collections.namedtuple('Config', 'arrows dice faces rerolls roles resolutions')

Role = collections.namedtuple('Role', 'name')
sheriff = Role("Sheriff")
vice = Role("Vice")
outlaw = Role("Outlaw")
renegade = Role("Renegade")

class Player:
    def __init__(self, name, role):
        self.health = 8
        self.damage = 0
        self.arrows = 0
        self.name = name
        self.role = role
        if self.role is sheriff:
            self.health += 2

    @property
    def dead(self):
        return self.damage >= self.health

class Beer:
    name = "Beer"

    def __call__(self, game):
        player = yield ChoosePlayer(game.players)
        game.heal(player, 1)

class Gatlings:
    name = "Gatlings"

    def __call__(self, game):
        for p in game.players:
            if p is game.current_player:
                game.remove_arrows(p, p.arrows)
            else:
                yield from game.damage(p, 1)
        # TODO: Turn `None`s into empty iterables automatically.
        return ()

class Shoot:
    def __init__(self, distance):
        self.distance = distance

    @property
    def name(self):
        return "Shoot {}".format(self.distance)

    def __call__(self, game):
        player = yield ChoosePlayer(game.players_by_distance(self.distance))
        yield from game.damage(player, 1)

class Game:
    config = Config(
        arrows=9,
        dice=5,
        faces=[arrow, beer, dynamite, gatling, shoot_1, shoot_2],
        rerolls=2,
        roles={
            # TODO: Generate keys automatically.
            3: [sheriff, outlaw, renegade],
            4: [sheriff, outlaw, renegade, renegade],
            5: [sheriff, vice, outlaw, outlaw, renegade],
            6: [sheriff, vice, outlaw, outlaw, renegade, renegade],
            7: [sheriff, vice, vice, outlaw, outlaw, outlaw, renegade],
            8: [sheriff, vice, vice, outlaw, outlaw, outlaw, renegade, renegade],
        },
        resolutions=[
            (Pool([beer]), Beer()),
            (Pool([gatling] * 3), Gatlings()),
            (Pool([shoot_1]), Shoot(1)),
            (Pool([shoot_2]), Shoot(2)),
        ],
    )

    def __init__(self, players, seed):
        self.arrows = self.config.arrows
        self.random = random.Random(seed)
        roles = list(self.config.roles[players])
        self.random.shuffle(roles)
        self.players = [
            Player("Player {}".format(i), roles.pop())
            for i in range(players)
        ]
        for p in self.players:
            if p.role is sheriff:
                self.current_player = p

    def play(self):
        yield GameStarted(self.players)

        while True:
            if self.gameover:
                break

            yield from self.turn()

            for i, p in enumerate(self.players):
                if p is self.current_player:
                    self.current_player = self.players[(i + 1) % len(self.players)]
                    break

    def turn(self):
        yield TurnStarted(self.current_player)

        try:
            # Roll.
            pool, stop = yield from self.roll(Pool([]), self.config.dice)
            for _ in range(self.config.rerolls):
                if stop:
                    break

                reroll = yield ChooseDice(pool.filter(self.rerollable))

                # No need to keep rerolling.
                if not len(reroll):
                    break

                pool.remove(reroll)
                rerolled, stop = yield from self.roll(pool, len(reroll))
                pool.add(rerolled)

            # Resolve.
            while True:
                actions = self.resolutions(pool)

                # Nothing left to do.
                if not actions:
                    break

                # TODO: Push `list` into ChooseAction.
                action = yield ChooseAction(list(actions))
                with self.check_alive:
                    # TODO: Allow the user to cancel.
                    yield from action(self)
                    pool.remove(actions[action])
        except EndTurn:
            pass

    def roll(self, pool, n):
        stop = False
        rolled = Pool(self.random.choice(self.config.faces) for _ in range(n))

        yield DiceRolled(rolled)

        with self.check_alive:
            self.give_arrows(self.current_player, rolled[arrow])

            if pool[dynamite] + rolled[dynamite] >= 3:
                yield Dynamites(self.current_player)
                yield from self.damage(self.current_player, 1)
                stop = True

        return rolled, stop

    def resolutions(self, pool):
        return {a: p for p, a in self.config.resolutions if p <= pool}

    def give_arrows(self, player, n):
        for _ in range(n):
            with self.check_alive:
                self.arrows -= 1
                player.arrows += 1
                if self.arrows == 0:
                    yield IndianAttack()
                    self.arrows = self.config.arrows
                    for p in self.players:
                        yield from self.damage(p, p.arrows)
                        p.arrows = 0

    def remove_arrows(self, player, n):
        n = min(player.arrows, n)
        player.arrows -= n
        self.arrows += n

    def damage(self, player, damage):
        player.damage += damage
        if player.dead:
            yield Died(player)
            self.players.remove(player)

    def heal(self, player, health):
        player.damage = max(player.damage - 1, 0)

    def rerollable(self, face):
        return face != dynamite

    def players_by_distance(self, distance):
        ps = len(self.players)
        ci = self.players.index(self.current_player)
        return [
            p
            for i, p in enumerate(self.players)
            if abs(i - ci) in {distance, ps - distance}
        ]

    @property
    @contextlib.contextmanager
    def check_alive(self):
        yield
        if self.current_player.dead or self.gameover:
            raise EndTurn

    @property
    def gameover(self):
        by_role = collections.defaultdict(set)
        for p in self.players:
            by_role[p.role].add(p)
        if len(self.players) == 1 and by_role[renegade]:
            return True
        elif not by_role[sheriff]:
            return True
        elif not by_role[outlaw] and not by_role[renegade]:
            return True
        else:
            return False

def run_input(f):
    g = f()
    value = None
    while g:
        try:
            action = g.send(value)
            value = None
        except StopIteration as e:
            return e.value
        if isinstance(action, ChooseDice):
            pool, = action
            chosen = collections.Counter()
            unchosen = collections.Counter(pool.dice)
            keys = {k.name[0].lower(): k for k in unchosen}

            print(", ".join(
                "[{}]{}: {}".format(k0, k.name[1:], unchosen[k])
                for k0, k in sorted(keys.items()))
            )
            ks = input("reroll: ")
            for k in ks:
                try:
                    k = keys[k]
                    if unchosen[k] <= 0: continue
                    unchosen[k] -= 1
                    chosen[k] += 1
                except KeyError:
                    pass

            value = Pool(chosen)
        elif isinstance(action, ChooseAction):
            choices, = action
            for i, c in enumerate(choices):
                print("[{}] {}".format(i, c.name))
            while True:
                i = input("action: ")
                try:
                    value = choices[int(i)]
                except (IndexError, ValueError):
                    pass
                else:
                    break
        elif isinstance(action, ChoosePlayer):
            players, = action
            for i, p in enumerate(players):
                print("[{}] {} ({})".format(i, p.name, p.health - p.damage))
            while True:
                i = input("player: ")
                try:
                    value = players[int(i)]
                except (IndexError, ValueError):
                    pass
                else:
                    break
        elif isinstance(action, GameStarted):
            players, = action
            print("Start")
            for p in players:
                print("{} ({})".format(p.name, p.role.name))
        elif isinstance(action, TurnStarted):
            player, = action
            print("{}'s turn".format(player.name))
        elif isinstance(action, DiceRolled):
            dice, = action
            print(", ".join(
                "{}× {}".format(v, k.name)
                for k, v in sorted(dice.dice.items())
            ))
        elif isinstance(action, Dynamites):
            player, = action
            print("Dynamites!")
        elif isinstance(action, IndianAttack):
            print("Indian Attack!")
        elif isinstance(action, Died):
            player, = action
            print("{} died".format(player.name))
        else:
            raise ValueError("Unknown action: {!r}".format(action))

if __name__ == '__main__':
    game = Game(3, 0)
    run_input(game.play)
